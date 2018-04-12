from mycroft.configuration.config import Configuration, LocalConf, USER_CONFIG
from mycroft.skills.core import MainModule
from mycroft.util.parse import match_one
from mycroft.messagebus.message import Message
from mycroft.util.log import LOG
from os.path import exists, expanduser, join, isdir
from os import makedirs, listdir, remove
import requests
import subprocess
import pip
from git import Repo
from git.cmd import Git
from time import sleep


__author__ = "JarbasAI"


class MycroftSkillsManager(object):
    DEFAULT_SKILLS = {}
    SKILLS_MODULES = "https://raw.githubusercontent.com/MycroftAI/mycroft-skills/master/.gitmodules"
    SKILLS_DEFAULTS_URL = "https://raw.githubusercontent.com/MycroftAI/mycroft-skills/master/DEFAULT-SKILLS"

    def __init__(self, emitter=None, skills_config=None, defaults_url=None, modules_url=None):
        self._skills_config = skills_config
        self.modules_url = modules_url or self.SKILLS_MODULES
        self.defaults_url = defaults_url or self.SKILLS_DEFAULTS_URL
        self.skills = {}
        self.default_skills = {}
        LOG.info("platform: " + self.platform)
        self.prepare_msm()
        self.bind(emitter)

    def bind(self, emitter):
        self.emitter = emitter

    def send_message(self, m_type, m_data=None, m_context=None):
        m_data = m_data or {}
        m_context = m_context or {"source": "py_msm"}
        if self.emitter is not None:
            self.emitter.emit(Message(m_type, m_data, m_context))
        else:
            LOG.warning("no messagebus emitter provided, message not sent")
            message = {"type": m_type, "data": m_data, "context": m_context}
            print message["type"], message["data"]

    @property
    def platform(self):
        return Configuration.get().get("enclosure", {}).get("platform", "desktop")

    @property
    def skills_dir(self):
        skills_dir = self.skills_config.get("directory", '/opt/mycroft/skills')

        # find home dir
        if "~" in skills_dir:
            skills_dir = expanduser(skills_dir)

        return skills_dir

    @property
    def skills_config(self):
        return self._skills_config or Configuration.get().get("skills", {"directory": '/opt/mycroft/skills'})

    @property
    def installed_skills(self):
        skills = []
        for skill in self.skills:
            if self.skills[skill].get("installed"):
                skills.append(skill)
        return skills

    def get_default_skills(self):
        """ get default skills list from url """
        LOG.info("retrieving default skills list")
        defaults = {}
        try:
            # get core and common skillw
            text = requests.get(self.defaults_url).text
            core = text.split("# core")[1]
            core, common = core.split("# common")
            core = [c for c in core.split("\n") if c]
            common = [c for c in common.split("\n") if c]
        except:
            core = common = []
        defaults["core"] = core
        defaults["common"] = common
        # get picroft
        try:
            text = requests.get(self.defaults_url + ".picroft").text
            picroft = text.split("# picroft")[1]
            picroft = [c for c in picroft.split("\n") if c]
        except:
            picroft = []
        defaults["picroft"] = picroft
        # get kde
        try:
            text = requests.get(self.defaults_url+".kde").text
            kde = text.split("# desktop")[1]
            kde = [c for c in kde.split("\n") if c]
        except:
            kde = []
        defaults["desktop"] = kde
        # get mark 1
        try:
            text = requests.get(self.defaults_url+".mycroft_mark_1").text
            mk1 = text.split("# mark 1")[1]
            mk1 = [c for c in mk1.split("\n") if c]
        except:
            mk1 = []
        defaults["mycroft_mark_1"] = mk1
        # on error use hard coded defaults
        LOG.info("default skills: " + str(defaults))
        return defaults or self.DEFAULT_SKILLS

    def prepare_msm(self):
        """ prepare msm execution """

        # create skills dir if missing
        if not exists(self.skills_dir):
            LOG.info("creating skills dir")
            makedirs(self.skills_dir)

        # update default skills list
        self.default_skills = self.get_default_skills()

        # scan skills folder
        self.scan_skills_folder()

        # scan skills repo
        self.scan_skills_repo()

        if self.platform in ["picroft", "mycroft_mark_1"]:
            pass
            # TODO permissions stuff

    def scan_skills_folder(self):
        """ scan installed skills """
        LOG.info("scanning installed skills")
        skills = []
        if exists(self.skills_dir):
            # checking skills dir and getting all skills there
            skill_list = [folder for folder in filter(
                lambda x: isdir(join(self.skills_dir, x)),
                listdir(self.skills_dir))]
            for skill_folder in skill_list:
                skills.append(skill_folder)
                self.read_skill_folder(skill_folder)
        LOG.info("scanned: " + str(skills))
        return skills

    def scan_skills_repo(self):
        """ get skills list from skills repo """
        LOG.info("scanning skills repo")
        text = requests.get(self.modules_url).text
        modules = text.split('[submodule "')
        skills = []
        for module in modules:
            if not module:
                continue
            name = module.split('"]')[0].strip()
            skills.append(name)
            url = module.split('url = ')[1].strip()
            skill_data = self.url_info(url)
            self.skills[skill_data["folder"]] = skill_data
        LOG.info("scanned: " + str(skills))
        return skills

    def is_skill(self, skill_folder):
        """
            Check if folder is a skill and perform mapping.
        """
        LOG.info("checking if " + skill_folder + " is a skill")
        path = join(self.skills_dir, skill_folder)
        # check if folder is a skill (must have __init__.py)
        if not MainModule + ".py" in listdir(path):
            LOG.warning("not a skill!")
            return False
        return True

    def read_skill_folder(self, skill_folder):
        if not self.is_skill(skill_folder):
            return False
        path = join(self.skills_dir, skill_folder)
        if skill_folder not in self.skills:
            self.skills[skill_folder] = {"id": hash(path)}
        git_url = self.git_from_folder(path)
        if git_url:
            author = git_url.split("/")[-2]
        else:
            author = "unknown"
        self.skills[skill_folder]["path"] = path
        self.skills[skill_folder]["folder"] = skill_folder
        if "name" not in self.skills[skill_folder].keys():
            self.skills[skill_folder]["name"] = skill_folder
        self.skills[skill_folder]["repo"] = git_url
        self.skills[skill_folder]["author"] = author
        self.skills[skill_folder]["installed"] = True
        return True

    def install_defaults(self):
        """ installs the default skills, updates all others """
        for skill in self.default_skills["core"]:
            LOG.info("installing core skills")
            self.install_by_name(skill)
        for skill in self.default_skills["common"]:
            LOG.info("installing common skills")
            self.install_by_name(skill)
        for skill in self.default_skills.get(self.platform, []):
            LOG.info("installing platform specific skills")
            self.install_by_name(skill)
        self.update_skills()

    def install_by_url(self, url):
        """ installs from the specified github repo """
        self.github_url_check(url)
        data = self.url_info(url)
        skill_folder = data["folder"]
        path = data["path"]
        self.send_message("msm.installing", data)
        try:
            if exists(path):
                LOG.info("skill exists, updating")
                g = Git(path)
                g.pull()
            else:
                LOG.info("Downloading skill: " + url)
                Repo.clone_from(url, path)
            if skill_folder not in self.skills:
                self.skills[skill_folder] = data
            # TODO get error codes from installing requirements
            self.run_requirements_sh(skill_folder)
            self.run_pip(skill_folder)
            self.skills[skill_folder]["installed"] = True
            self.send_message("msm.install.succeeded", data)
        except Exception as e:
            data["error"] = e
            self.send_message("msm.install.failed", data)
        self.send_message("msm.installed")

    def install_by_name(self, name):
        """ installs the mycroft-skill matching <name> """
        LOG.info("searching skill by name: " + name)
        skill_folder = self.match_name_to_folder(name)
        if skill_folder is not None:
            skill = self.skills[skill_folder]
            return self.install_by_url(skill["repo"])
        data = {"name": name}
        self.send_message("msm.installing", data)
        data["error"] = "skill not found"
        self.send_message("msm.install.failed", data)
        self.send_message("msm.installed", data)
        return False

    def update_skills(self):
        """ update all installed skills """
        LOG.info("updating installed skills")
        self.send_message("msm.updating")
        for skill in self.skills:
            if self.skills[skill]["installed"]:
                # TODO check if user modified before updating
                LOG.info("updating " + skill)
                self.install_by_url(self.skills[skill]["repo"])
        self.send_message("msm.updated")

    def remove_by_url(self, url):
        """ removes the specified github repo """
        LOG.info("searching skill by github url: " + url)
        data = self.url_info(url)
        self.send_message("msm.removing", data)
        if data["folder"] in self.skills:
            for skill in self.skills:
                if url == self.skills[skill]["repo"]:
                    LOG.info("found skill!")
                    if self.skills[skill]["installed"]:
                        remove(data["path"])
                        self.send_message("msm.remove.succeeded", data)
                        self.send_message("msm.removed", data)
                        return True
                    else:
                        LOG.warning("skill not installed!")
                        data["error"] = "skill not installed"
                        self.send_message("msm.remove.failed", data)
                        self.send_message("msm.removed", data)
        else:
            LOG.warning("skill not found!")
            data["error"] = "skill not found"
            self.send_message("msm.remove.failed", data)
        self.send_message("msm.removed", data)
        return False

    def remove_by_name(self, name):
        """ removes the specified skill folder name """
        skill_folder = self.match_name_to_folder(name)

        if skill_folder:
            data = self.skills[skill_folder]
            self.send_message("msm.removing", data)
            installed = self.skills[skill_folder]["installed"]
            self.skills[skill_folder]["installed"] = False
            if not installed:
                LOG.warning("skill is not installed!")
                # TODO error code
                data["error"] = "skill not installed"
                self.send_message("msm.remove.failed", data)
            else:
                remove(self.skills[skill_folder]["path"])
                LOG.info("skill removed")
                self.send_message("msm.remove.succeeded", self.skills[skill_folder])
            self.send_message("msm.removed", self.skills[skill_folder])
            return True
        else:
            data = {"name": name}
            self.send_message("msm.removing", data)

        data["error"] = "skill not found"
        self.send_message("msm.remove.failed", data)
        self.send_message("msm.removed", data)
        return False

    def list_skills(self):
        """ list all mycroft-skills in the skills repo and installed """
        # scan skills folder
        self.scan_skills_folder()
        # scan skills repo
        self.scan_skills_repo()
        return self.skills

    def url_info(self, url):
        """ shows information about the skill in the specified repo """
        LOG.info("getting skill info from github url: " + url)
        for skill in self.skills:
            if url == self.skills[skill]["repo"]:
                LOG.info("found skill!")
                return self.skills[skill]
        self.github_url_check(url)
        skill_folder = name = url.split("/")[-1]
        if skill_folder[-4:] == '.git':
            name = skill_folder = skill_folder[:-4]
        skill_path = join(self.skills_dir, skill_folder)
        skill_id = hash(skill_path)
        skill_author = url.split("/")[-2]
        installed = skill_folder in self.installed_skills
        return {"repo": url, "folder": skill_folder, "path": skill_path, "id": skill_id, "author": skill_author, "name": name, "installed": installed}

    def name_info(self, name):
        """ shows information about the skill matching <name> """
        LOG.info("searching skill by name: " + name)
        skill = self.match_name_to_folder(name)
        if skill is not None:
            return self.skills[skill]
        LOG.warning("skill not found")
        return {}

    def run_pip(self, skill_folder):
        LOG.info("running pip for: " + skill_folder)
        skill = self.skills[skill_folder]
        # no need for sudo if in venv
        # TODO handle sudo if not in venv
        # TODO check hash before re running
        if exists(join(skill["path"], "requirements.txt")):
            pip_code = pip.main(['install', '-r', join(skill["path"], "requirements.txt")])
            # TODO parse pip code
            return True
        return False

    def run_requirements_sh(self, skill_folder):
        LOG.info("running requirements.sh for: " + skill_folder)
        skill = self.skills[skill_folder]
        reqs = join(skill["path"], "requirements.sh")
        # TODO check hash before re running
        if exists(reqs):
            # make exec
            subprocess.call((["chmod", "+x", reqs]))
            # handle sudo
            if self.platform in ["desktop", "kde", "jarbas"]:
                # gksudo
                output = subprocess.check_output(["gksudo", "bash", reqs])
            else:  # no sudo
                output = subprocess.check_output(["bash", reqs])
            return True
        return False

    def match_name_to_folder(self, name):
        LOG.info("searching skill by name: " + name)
        folders = self.skills.keys()
        names = [self.skills[skill]["name"] for skill in folders]
        f_skill, f_score = match_one(name, folders)
        n_skill, n_score = match_one(name, names)
        if n_score > 0.6:
            for s in self.skills:
                if self.skills[s]["name"] == n_skill:
                    LOG.info("found skill by name")
                    return s
        elif f_score > 0.6:
            LOG.info("found skill by folder name")
            return f_skill
        return None

    @staticmethod
    def git_from_folder(path):
        try:
            website = subprocess.check_output(["git", "remote", "-v"], cwd=path)
            website = website.replace("origin\t", "").replace(" (fetch)", "").split("\n")[0]
        except:
            website = None
        return website

    @staticmethod
    def github_url_check(url=""):
        if not url.startswith("https://github.com"):
            raise AttributeError("this url does not seem to be form github: " + url)

    # handling skills config

    def remove_from_priority_list(self, skill_name):
        skill_folder = self.match_name_to_folder(skill_name)
        if skill_folder is None:
            LOG.error("could not find skill to remove from priority list")
            return False
        config = self.skills_config
        if "priority_skills" not in config:
            config["priority_skills"] = []
        if skill_folder in config["priority_skills"]:
            if not self.skills[skill_folder]["installed"]:
                LOG.debug("removing skill from priority list, but it is not installed")
            config["priority_skills"].remove(skill_folder)
            LOG.info("Skill removed  from priority list: " + skill_folder)
            self.update_skills_config(config)
            self.send_message("skill.deprioritized")
        else:
            LOG.info("Skill is not in priority list: " + skill_folder)
        return True

    def add_to_priority_list(self, skill_name):
        skill_folder = self.match_name_to_folder(skill_name)
        if skill_folder is None:
            LOG.error("could not find skill to add to priority list")
            return False
        if not self.skills[skill_folder]["installed"]:
            LOG.debug("Adding skill to priority list, but it is not installed")

        config = self.skills_config
        if "priority_skills" not in config:
            config["priority_skills"] = []
        if skill_folder not in config["priority_skills"]:
            config["priority_skills"].append(skill_folder)
            LOG.info("Skill added to priority list: " + skill_folder)
            self.update_skills_config(config)
            self.send_message("skill.prioritized")
        else:
            LOG.info("Skill already in priority list: " + skill_folder)
        return True

    def remove_from_blacklist(self, skill_name):
        skill_folder = self.match_name_to_folder(skill_name)
        if skill_folder is None:
            LOG.error("could not find skill to unblacklist")
            return False

        config = self.skills_config
        if "blacklisted_skills" not in config:
            config["blacklisted_skills"] = []
        if skill_folder in config["blacklisted_skills"]:
            if not self.skills[skill_folder]["installed"]:
                LOG.debug("UnBlacklisting skill, but it is not installed")
            config["blacklisted_skills"].remove(skill_folder)
            LOG.info("Skill UnBlacklisted: " + skill_folder)
            self.update_skills_config(config)
            self.send_message("skill.whitelisted")
        else:
            LOG.info("Skill is not in blacklist: " + skill_folder)
        return True

    def add_to_blacklist(self, skill_name):
        skill_folder = self.match_name_to_folder(skill_name)
        if skill_folder is None:
            LOG.error("could not find skill to blacklist")
            return False

        if not self.skills[skill_folder]["installed"]:
            LOG.debug("Blacklisting skill, but it is not installed")

        config = self.skills_config
        if "blacklisted_skills" not in config:
            config["blacklisted_skills"] = []

        if skill_folder not in config["blacklisted_skills"]:
            config["blacklisted_skills"].append(skill_folder)
            LOG.info("Skill Blacklisted: " + skill_folder)
            self.send_message("skill.blacklisted", self.skills[skill_folder])
            self.update_skills_config(config)
        else:
            LOG.info("Skill already Blacklisted: " + skill_folder)
        return True

    def change_skills_directory(self, skills_dir):
        config = self.skills_config
        config["directory"] = skills_dir
        # create skills dir if missing
        if not exists(skills_dir):
            LOG.info("creating skills dir")
            makedirs(skills_dir)
        self.update_skills_config(config)

    def update_skills_config(self, config=None):
        conf = LocalConf(USER_CONFIG)
        conf['skills'] = config or self.skills_config
        conf.store()
        self.send_message("skills.config.updated")

    def reload_skill(self, skill_name):
        skill_folder = self.match_name_to_folder(skill_name)
        self.send_message("skill.reloading", {"name": skill_folder})
        if skill_folder is None:
            LOG.error("Could not find skill to reload: " + skill_name)
            self.send_message("skill.reload.failed", {"name": skill_folder, "error": "skill not found"})
            return False
        path = self.skills[skill_folder]["path"]+"/reloading.tmp"
        with open(path, "w") as f:
            f.write(" ")
        sleep(2)
        remove(path)
        self.send_message("skill.reloaded", self.skills[skill_folder])
        return True


class JarbasSkillsManager(MycroftSkillsManager):
    SKILLS_MODULES = "https://raw.githubusercontent.com/JarbasAl/jarbas_skills_repo/master/"
    SKILLS_DEFAULTS_URL = "https://raw.githubusercontent.com/JarbasAl/jarbas_skills_repo/master/DEFAULT_SKILLS"

    def __init__(self, emitter=None, skills_config=None, defaults_url=None, modules_url=None):
        self.msm = MycroftSkillsManager(emitter, skills_config)
        defaults_url = defaults_url or self.SKILLS_DEFAULTS_URL
        modules_url = modules_url or self.SKILLS_MODULES
        super(JarbasSkillsManager, self).__init__(emitter, skills_config, defaults_url, modules_url)

    @property
    def mycroft_repo_skills(self):
        """ get skills list from mycroft skills repo """
        LOG.info("scanning Mycroft skills repo")
        return self.msm.scan_skills_repo()

    @property
    def default_skills(self):
        """ get default skills list from url """
        LOG.info("retrieving default jarbas skills list")
        defaults = {}
        try:
            # get core and common skills
            text = requests.get(self.defaults_url).text
            core = text.split("# core")[1]
            core, common = core.split("# common")
            core = [c for c in core.split("\n") if c]
            common = [c for c in common.split("\n") if c]
        except:
            core = common = []
        defaults["core"] = core
        defaults["common"] = common
        # get picroft
        try:
            text = requests.get(self.defaults_url + ".picroft").text
            picroft = text.split("# picroft")[1]
            picroft = [c for c in picroft.split("\n") if c]
        except:
            picroft = []
        defaults["picroft"] = picroft
        # get kde
        try:
            text = requests.get(self.defaults_url + ".kde").text
            kde = text.split("# desktop")[1]
            kde = [c for c in kde.split("\n") if c]
        except:
            kde = []
        defaults["desktop"] = kde
        # get mark 1
        try:
            text = requests.get(self.defaults_url + ".mycroft_mark_1").text
            mk1 = text.split("# mark 1")[1]
            mk1 = [c for c in mk1.split("\n") if c]
        except:
            mk1 = []
        defaults["mycroft_mark_1"] = mk1
        # get jarbas
        try:
            text = requests.get(self.defaults_url + ".jarbas").text
            jarbas = text.split("# jarbas")[1]
            jarbas = [c for c in jarbas.split("\n") if c]
        except:
            jarbas = []
        defaults["jarbas"] = jarbas
        # on error use hard coded defaults
        LOG.info("default jarbas skills: " + str(defaults))
        return defaults or self.DEFAULT_SKILLS

    def scan_skills_repo(self):
        """ get skills list from skills repo """
        LOG.info("scanning Jarbas skills repo")
        platforms = ["core", "common", "kde", "jarbas", "desktop", "picroft",  "mycroft_mark_1"]
        scanned = []
        for platform in platforms:
            text = requests.get(self.modules_url+platform+".txt").text
            skills = text.splitlines()
            for s in skills:
                name, url = s.split(",")
                if not url:
                    url = self.msm.name_info(name).get("repo")
                    if not url:
                        continue
                scanned.append(name)
                skill_folder = url.split("/")[-1]
                skill_path = join(self.skills_dir, skill_folder)
                skill_id = hash(skill_path)
                skill_author = url.split("/")[-2]
                installed = False
                if skill_folder in self.installed_skills:
                    installed = True
                self.skills[skill_folder] = {"repo": url, "folder": skill_folder, "path": skill_path, "id": skill_id,
                                             "author": skill_author, "name": name, "installed": installed}

            LOG.info("scanned " + platform + ": " + str(skills))
        return scanned
