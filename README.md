# vk_autosocial
Script which is working with Social Network VK and automate some processes via API.

Below you can find currently implemented features with small description.

# Public Management
images_getter(folder, vk_session)

Method which is consuming images via API to the hard disk.

folder - name of the folder, where images will be stored. tmp/ folder by default in the same place with script.
vk_session - established VK session

check_for_duplicates(folder)

Method which is checking tmp/ folder for possible duplicates and delete them if existed.

stories_publisher(folder, vk_session)

Method looking into tmp/ folder, finding images with height and weight required for stories and make 10 stories.
Images from tmp/ will be deleted.

post_publisher(folder, vk_session)

Posts maker. Creating posts every N hour and put them into delayed posts.

# Page Management
friends_list_cleaner(vk_session, week)

Cleanup of them friends list by last activity.

subscription_cleaner(vk_session)

Delete all unnecessary subscriptions from your account. 

friends_adder(vk_session, month, sex)

Sending invitations for friends to the people from GROUPS (in conf.cfg) by gender.

sex=1 - female
sex=2 - male

# Communities analysing
community_members_analyser(vk_session, month, week, chart_folder)
community_posts_analyser(vk_session, month, week, chart_folder)

Two methods which are using Pandas for making charts into charts/ folder. 
Methods will provide statistics on the users activities and reactions on the posts in the chosen group (GROUP_TO_CHECK from conf.cfg)