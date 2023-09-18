# vk_autosocial

**vk_autosocial** is a script designed for working with the VK (VKontakte) social network to automate various processes using the VK API. Below, you'll find a list of currently implemented features along with brief descriptions.

## Public Management

### `images_getter(folder, vk_session)`

This method consumes images via the VK API and saves them to your hard disk.

- `folder`: Name of the folder where images will be stored. By default, it uses the 'tmp/' folder located in the same directory as the script.
- `vk_session`: An established VK session.

### `check_for_duplicates(folder)`

This method checks the 'tmp/' folder for possible duplicate images and deletes them if they exist.

### `stories_publisher(folder, vk_session)`

Automates the creation of VK stories. It scans the 'tmp/' folder for images with the required dimensions for VK stories and generates up to 10 stories. Images from 'tmp/' will be deleted after the stories are created.

### `post_publisher(folder, vk_session)`

Creates and schedules posts at specified intervals. Posts are added to the delayed posts section.

## Page Management

### `friends_list_cleaner(vk_session, week)`

Cleans up your friends list based on their last activity. Friends who haven't been active within the specified time frame (in weeks) are removed.

### `subscription_cleaner(vk_session)`

Deletes unnecessary subscriptions from your VK account.

### `friends_adder(vk_session, month, sex)`

Sends friend invitations to people from specified groups (configured in `conf.cfg`) based on their gender.

- `sex=1` - Female
- `sex=2` - Male

## Communities Analysis

### `community_members_analyser(vk_session, month, week, chart_folder)`
### `community_posts_analyser(vk_session, month, week, chart_folder)`

Utilizes Pandas to generate charts in the 'charts/' folder. These charts offer insights into user activities and reactions to posts in the selected group (configured in `conf.cfg`).
