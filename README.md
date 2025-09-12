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
Create configuration file:

```bash
cp conf.cfg.example conf.cfg
```
Edit conf.cfg with your VK API credentials and settings:

```ini
[AUTH]
phone_number = your_phone_number
password = your_password
api_version = 5.199

[GROUPS]
YOUR_GROUP = your_main_group_id
GROUPS = group_id1, group_id2, group_id3
GROUP_TO_CHECK = group_id_for_analytics

[POSTS]
TEXT = path_to_text_file.txt
START_TIME = 
GROUP_CHAT_REMINDER = 7
GROUP_CHAT_REMINDER_TEXT = Join our chat: {} {} {}
GROUP_CHAT_LINK = vk.cc/your_chat_link
HASHTAGS = #your #hashtags #here
```
Get VK Access Token:

Create a standalone application at https://vk.com/apps?act=manage

Obtain an access token with necessary permissions:

wall - for posting

photos - for image uploads

groups - for group management

friends - for friend management

stories - for story posting

🚀 Usage
Basic Automation
bash
python main.py
Manual Operations (uncomment in main())
python
# Download images from groups
asyncio.run(images_getter_async(folder='tmp', vk_session=vk_session))

# Check for duplicate images
check_for_duplicates(folder='tmp')

# Publish posts with custom delay
post_publisher(folder='tmp', vk_session=vk_session, time_delay=14400)

# Clean wall posts
wall_cleaner(vk_session)

# Analyze community members
community_members_analyser(vk_session, month, week, 'charts')

# Analyze community posts
community_posts_analyser(vk_session, week, 'charts')
Command Line Options
The script supports various functions through the main() function. Uncomment the desired operations:

Image Management: Download and process images from groups

Post Scheduling: Automatically schedule posts with configurable intervals

Community Analytics: Generate demographic and engagement reports

Friend Management: Automate friend adding/removing based on activity

📁 Project Structure
text
vk_autosocial/
├── main.py                 # Main application entry point
├── requirements.txt        # Python dependencies
├── conf.cfg               # Configuration file
├── db_text.txt            # Database for post texts
├── tmp/                   # Temporary image storage
├── charts/                # Analytics charts output
├── logs/                  # Application logs (auto-created)
└── README.md              # This file
🔧 Key Functions
Content Management
images_getter_async(): Download images from specified VK groups

check_for_duplicates(): Remove duplicate images using perceptual hashing

post_publisher(): Schedule and publish posts with images

Community Analytics
community_members_analyser(): Analyze group demographics and activity

community_posts_analyser(): Analyze post engagement metrics

plot_creator(): Generate visualization charts

Account Management
friends_adder(): Add friends from specified groups

friends_list_cleaner(): Remove inactive friends

subscription_cleaner(): Clean up outgoing friend requests

⚠️ Important Notes
Rate Limiting: The script includes built-in delays to avoid VK API limits

Error Handling: Comprehensive error handling for network issues and API limits

Image Processing: Automatic validation and removal of corrupt images

Configuration: Ensure all settings in conf.cfg are properly configured

Permissions: Your VK app must have the necessary permissions for all operations

🔒 Security
Never commit your conf.cfg file to version control

Use environment variables for sensitive data in production

Regularly rotate access tokens

📊 Analytics
The script generates comprehensive analytics including:

User demographics (gender, age distribution)

Activity metrics (active vs. inactive users)

Post performance (views, likes, reposts)

Visualization charts in the charts/ directory

🤝 Contributing
Fork the repository

Create a feature branch

Make your changes

Add tests if applicable

Submit a pull request

📄 License
This project is licensed under the MIT License - see the LICENSE file for details.

🆘 Support
For issues and questions:

Check the configuration settings

Verify API permissions

Ensure all dependencies are installed

Check VK API status for any service disruptions

Note: This tool should be used in compliance with VK's Terms of Service. Automated activities should respect rate limits and community guidelines.