import requests
import vk_api
from utils import get_logger

logger = get_logger(__name__)


class VKAuth:
    def __init__(self, phone_number: str, password: str, api_version: str):
        self.phone_number = phone_number
        self.password = password
        self.api_version = api_version

    def session_maker(self):
        vk_session = vk_api.VkApi(
            login=self.phone_number,
            password=self.password,
            api_version=self.api_version
        )

        try:
            try:
                vk_session.auth(token_only=True)
            except vk_api.AuthError as error_msg:
                logger.error('Problem with Auth observed. Error: ' + str(error_msg))
                exit()
        except requests.exceptions.ConnectionError as con_error_msg:
            logger.error('Network problem observed. Error: ' + str(con_error_msg))
            exit()
        logger.info('Connection was successfully established.')
        return vk_session
