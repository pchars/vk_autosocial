"""
API package for VK AutoSocial
"""

from .vk_auth import VKAuth
from .vk_client import VKClient

__all__ = [
    'VKAuth',
    'VKClient'
]
