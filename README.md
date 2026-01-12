# VK AutoSocial 🚀

A comprehensive automated social media management system for VKontakte (VK) that handles content curation, duplicate detection, automated posting, and community analytics.

## 📋 Features

- **Automated Content Management**: Download, process, and post images from VK groups
- **Smart Duplicate Detection**: Advanced perceptual hashing to prevent duplicate posts
- **Scheduled Posting**: Automatic scheduling with configurable intervals
- **Community Analytics**: Detailed member demographics and post performance analysis
- **Multi-Account Support**: Manage multiple VK accounts and groups
- **Async Operations**: High-performance asynchronous API calls and downloads
- **Content Curation**: Automatic friend management and subscription cleaning

## 🛠 Installation

1. **Clone the repository**:
```bash
git clone https://github.com/pchars/vk_autosocial.git
cd vk_autosocial
```
Create virtual environment:

```bash
# Linux/Mac
python -m venv .venv
source .venv/bin/activate
# Windows
.venv\Scripts\activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```
⚙️ Configuration

Create and edit conf.cfg with your VK API credentials and settings:

```ini
[GROUPS]
GROUPS=123456,789012,34567890
YOUR_GROUP=123456789
GROUP_TO_CHECK=123456789

[POSTS]
TEXT=db_text.txt
START_TIME=1768204800
GROUP_CHAT_LINK=https://url.of.chat/
GROUP_CHAT_REMINDER=6
HASHTAGS=#example #of #usage
GROUP_CHAT_REMINDER_TEXT=text_sample
[AUTH]
PHONE_NUMBER=+123456789012
PASSWORD=your_password
API_VERSION=5.199

[LOGGING]
LOG_LEVEL=debug
LOG_FILE=logs/app.log
LOG_FOLDER=logs
CONSOLE_OUTPUT=true

[FOLDERS]
IMAGE_FOLDER=images
CHART_FOLDER=charts
```

## 📁 Project Structure (OUTDATED)
```text
vk_autosocial/
├── main.py                # Main application entry point
├── requirements.txt       # Python dependencies
├── conf.cfg               # Configuration file
├── db_text.txt            # Database for post texts
├── tmp/                   # Temporary image storage
├── charts/                # Analytics charts output
├── logs/                  # Application logs (auto-created)
└── README.md              # This file
```


## 📊 Analytics
The script generates comprehensive analytics including:

- User demographics (gender, age distribution)
- Activity metrics (active vs. inactive users)
- Post performance (views, likes, reposts)
- Visualization charts in the charts/ directory

## 🤝 Contributing
- Fork the repository
- Create a feature branch
- Make your changes
- Add tests if applicable
- Submit a pull request

## 📄 License
This project is licensed under the MIT License - see the **LICENSE** file for details.

## 🆘 Support
For issues and questions:

- Check the configuration settings
- Verify API permissions
- Ensure all dependencies are installed
- Check VK API status for any service disruptions

Note: This tool should be used in compliance with VK's Terms of Service. Automated activities should respect rate limits and community guidelines.