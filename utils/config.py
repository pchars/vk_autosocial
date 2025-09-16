import configparser
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


@dataclass
class VKAuthConfig:
    phone_number: str
    password: str
    api_version: str = "5.199"


@dataclass
class GroupsConfig:
    groups: List[int]  # List of groups ID
    your_group: int  # Your public ID
    group_to_check: int  # Public for analytics


@dataclass
class PostsConfig:
    text: str = ""
    start_time: Optional[int] = None
    group_chat_link: str = ""
    group_chat_reminder: int = 6
    hashtags: str = ""
    group_chat_reminder_text: str = ""


@dataclass
class LoggingConfig:
    log_level: str = "info"
    log_file: Optional[Path] = None
    console_output: bool = True


@dataclass
class AppConfig:
    auth: VKAuthConfig
    groups: GroupsConfig
    posts: PostsConfig
    logging: LoggingConfig

    @classmethod
    def from_cfg_file(cls, cfg_path: Path = Path("conf.cfg")):
        """Download configuration from conf.cfg file"""
        config = configparser.ConfigParser()
        config.read(cfg_path)

        # Logging section
        log_section = config['LOGGING'] if 'LOGGING' in config else {}

        return cls(
            auth=VKAuthConfig(
                phone_number=config['AUTH']['PHONE_NUMBER'],
                password=config['AUTH']['PASSWORD'],
                api_version=config['AUTH'].get('API_VERSION', '5.199')
            ),
            groups=GroupsConfig(
                groups=[int(g.strip()) for g in config['GROUPS']['GROUPS'].split(',')],
                your_group=int(config['GROUPS']['YOUR_GROUP']),
                group_to_check=int(config['GROUPS']['GROUP_TO_CHECK'])
            ),
            posts=PostsConfig(
                text=config['POSTS']['TEXT'],
                start_time=int(config['POSTS']['START_TIME']) if config['POSTS']['START_TIME'] else None,
                group_chat_link=config['POSTS']['GROUP_CHAT_LINK'],
                group_chat_reminder=int(config['POSTS']['GROUP_CHAT_REMINDER']),
                hashtags=config['POSTS']['HASHTAGS'],
                group_chat_reminder_text=config['POSTS']['GROUP_CHAT_REMINDER_TEXT']
            ),
            logging=LoggingConfig(
                log_level=log_section.get('LOG_LEVEL', 'info'),
                log_file=Path(log_section['LOG_FILE']) if 'LOG_FILE' in log_section else None,
                console_output=log_section.getboolean('CONSOLE_OUTPUT', True)
            )
        )
