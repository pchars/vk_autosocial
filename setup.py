from setuptools import setup, find_packages

setup(
    name='vk_autosocial',
    version='0.1',
    packages=find_packages(),
    url='https://github.com/pchars/vk_autosocial',
    license='GPL-3.0',
    author='Alexander Geraskin',
    author_email='alexander.geraskin@gmail.com',
    description='VK AutoSocial Automation Tool',
    install_requires=[
        'aiohttp>=3.9.0,<4.0.0',
        'imagehash>=4.3.0,<5.0.0',
        'matplotlib>=3.8.0,<4.0.0',
        'pandas>=2.1.0,<3.0.0',
        'Pillow>=10.0.0,<11.0.0',
        'requests>=2.31.0,<3.0.0',
        'seaborn>=0.13.0,<0.14.0',
        'vk-api>=11.9.0,<12.0.0',
        'configparser>=5.3.0,<6.0.0',
        'olefile>=0.46'
    ],
    python_requires='>=3.10',
)