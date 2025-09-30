import unittest
from unittest.mock import patch, MagicMock

import requests
import vk_api
from api.vk_auth import VKAuth


class TestVKAuth(unittest.TestCase):

    def setUp(self):
        """Test data for the tests"""
        self.phone_number = "+79991234567"
        self.password = "test_password"
        self.api_version = "5.131"
        self.vk_auth = VKAuth(self.phone_number, self.password, self.api_version)

    @patch('api.vk_auth.vk_api.VkApi')
    @patch('api.vk_auth.logger')
    def test_session_maker_success(self, mock_logger, mock_vk_api):
        """Test with successful auth"""
        # Mock objects creation
        mock_vk_instance = MagicMock()
        mock_vk_api.return_value = mock_vk_instance

        # Calling testing method
        result = self.vk_auth.session_maker()

        # Checking that VkApi was created with required params
        mock_vk_api.assert_called_once_with(
            login=self.phone_number,
            password=self.password,
            api_version=self.api_version
        )

        # Checking that auth method was called
        mock_vk_instance.auth.assert_called_once_with(token_only=True)

        # Logger message check
        mock_logger.info.assert_called_once_with('Connection was successfully established.')

        self.assertEqual(result, mock_vk_instance)

    @patch('api.vk_auth.vk_api.VkApi')
    @patch('api.vk_auth.logger')
    def test_session_maker_auth_error(self, mock_logger, mock_vk_api_class):
        """Wrong auth test"""
        # Configuring mock to throw AuthError
        mock_vk_session = MagicMock()
        mock_vk_session.auth.side_effect = vk_api.AuthError("Invalid credentials")
        mock_vk_api_class.return_value = mock_vk_session

        with self.assertRaises(SystemExit):
            self.vk_auth.session_maker()

        mock_logger.error.assert_called_once_with(
            'Problem with Auth observed. Error: Invalid credentials'
        )

    @patch('api.vk_auth.vk_api.VkApi')
    @patch('api.vk_auth.logger')
    def test_session_maker_network_error(self, mock_logger, mock_vk_api_class):
        """Network issue test"""
        # Configuring mock to throw ConnectionError
        mock_vk_session = MagicMock()
        mock_vk_session.auth.side_effect = requests.exceptions.ConnectionError("Network problem observed")
        mock_vk_api_class.return_value = mock_vk_session

        with self.assertRaises(SystemExit):
            self.vk_auth.session_maker()

        mock_logger.error.assert_called_once_with(
            'Network problem observed. Error: Network problem observed'
        )


if __name__ == '__main__':
    unittest.main()
