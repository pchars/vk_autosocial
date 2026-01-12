import asyncio
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import requests
import seaborn as sns
import vk_api
from PIL import Image

from api import VKAuth, VKClient
from services import PersonalPageManager, ContentManager, ImageProcessor
from utils import AppConfig, setup_logging, get_logger, OSManagement

config = AppConfig.from_cfg_file()
setup_logging(
    log_level=config.logging.log_level,
    log_file=Path(f"{config.logging.log_folder}/{config.logging.log_file}"),
    console_output=config.logging.console_output
)
logger = get_logger(__name__)
logger.info("Application started successfully!")


async def main():
    try:
        # Creating folders
        folders = [
            config.folders.image_folder,
            config.logging.log_folder,
            config.folders.chart_folder
        ]
        base_dir = Path(__file__).parent
        text_file_path = config.posts.get_text_file_path(base_dir)

        # Constants
        week = int(time.time() - 604800)  # current time minus one week
        month = int(time.time() - 2678400)  # 31 день назад
        sex = 1  # gender for friends adder, 1 - female, 2 - male

        vk_auth = VKAuth(
            phone_number=config.auth.phone_number,
            password=config.auth.password,
            api_version=config.auth.api_version
        )

        os_manager = OSManagement()
        vk_session = vk_auth.session_maker()
        vk_client = VKClient(vk_session)
        pp_manager = PersonalPageManager(vk_client)
        c_manager = ContentManager(vk_client)

        # Create all required folders
        success = os_manager.ensure_multiple_folders(folders)

        if success:
            logger.info("All folders are ready to use")
        else:
            logger.warning("Some folders couldn't be created")

        # Public Management
        download_stats = await c_manager.download_images_from_groups(
            folder=config.folders.image_folder,
            group_ids=config.groups.groups,  # List of the groups from config file
            max_posts_per_group=100,
            max_concurrent=10
        )

        logger.info(f"Images downloaded: {download_stats['total_downloaded']}")

        image_processing = ImageProcessor(similarity_threshold=5, folder_path=config.folders.image_folder)
        image_processing.check_for_duplicates(config.folders.image_folder)
        logger.info("Image processing for duplicates was completed")
        logger.info("Post scheduler was started")
        await c_manager.post_publisher(
            folder=config.folders.image_folder,
            your_group=config.groups.your_group,
            start_time=config.posts.start_time,
            text=str(text_file_path)
        )
        logger.info("All posts were scheduled")

        # Wall cleaning
        wall_cleanup_stats = await c_manager.wall_cleaner(group_id=-config.groups.your_group,
                                                          delete_postponed=True, delete_published=True)
        logger.info(f"Wall cleanup completed: {wall_cleanup_stats['deleted_count']} removed")

        # Personal Page Management
        # Friends cleaning
        friends_removal_stats = await pp_manager.friends_remover(month)
        logger.info(f"Friend cleanup completed: {friends_removal_stats['deleted_count']} removed")
        # Friends adder
        await pp_manager.friends_adder(month, sex) # Add friends from GROUPS variable by gender and activity
        logger.info(f"Friend adding completed")
        # Friends Requests cleaning
        # 1 - requests which you sent to people, 0 - requests which people sent to you (your subscribers)
        friends_requests_removal_stats = await pp_manager.friends_requests_remover(1)
        logger.info(f"Friends requests cleanup completed: {friends_requests_removal_stats['deleted_count']} removed")
    except Exception as e:
        logger.error(f"Main execution failed: {e}")
        logger.error(f"Error type: {type(e).__name__}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        return

    # Public Management
    # stories_publisher(folder_name, vk_session)

    # Communities analyzing
    # community_members_analyser(vk_session, month, week, chart_folder)
    # community_posts_analyser(vk_session, week, chart_folder)

def stories_publisher(folder, vk_session):
    counter = 0
    # Upload the photo to the server
    for file in os.listdir(folder):
        image = Image.open(folder + '/' + file)

        # Get the size of the image
        width, height = image.size
        if height == 1080 and width <= 721:
            photo = open(folder + '/' + file, 'rb')
            group = config.groups.your_group
            # Get an upload URL for a photo
            vk = vk_session.get_api()
            upload_server = vk.stories.getPhotoUploadServer(group_id=int(group), add_to_news=1)['upload_url']
            # Upload the story
            response = requests.post(upload_server, files={'file': photo}).json()
            # Save the story
            try:
                vk.stories.save(upload_results=response['response']['upload_result'])
            except vk_api.exceptions.ApiError as e:
                logger.error(str(e))

            os.remove(folder + '/' + file)
            logger.info('Story was published successfully.')
            counter += 1
            if counter == 10:
                break


def community_members_analyser(vk_session, month, week, chart_folder):
    group = config.groups.group_to_check
    vk = vk_session.get_api()
    try:
        vk.groups.join(group_id=group)
    except vk_api.exceptions.ApiError:
        logger.warning('Script tried to join community. You already part of the group or public.')
    p = vk.groups.getMembers(group_id=int(group), offset=0, count=1)

    iter_count = int(math.ceil((float(p['count']) / 1000)))
    total_amount_of_participants = int(p['count'])
    male, female = 0, 0
    inactive_month, inactive_week, time_undefined, active_time = 0, 0, 0, 0
    bdate, bdate_missing, dates = [], 0, {}
    for count in range(0, iter_count):
        p = vk.groups.getMembers(group_id=int(group), offset=count * 1000,
                                 fields='bdate,city,contacts,country,domain,last_seen,sex',
                                 sort='id_asc')
        for item in p['items']:
            if item['sex'] == 1:
                female += 1
            elif item['sex'] == 2:
                male += 1
            if 'last_seen' in item.keys():
                if item['last_seen']['time'] <= month:
                    inactive_month += 1
                elif item['last_seen']['time'] <= week:
                    inactive_week += 1
                else:
                    active_time += 1
            else:
                time_undefined += 1
            if 'bdate' in item.keys():
                try:
                    bdate.append(item['bdate'].split('.')[2])
                except IndexError:
                    bdate_missing += 1
            else:
                bdate_missing += 1

    year_count = 1902
    while year_count != 2023:
        count = 0
        for year in bdate:
            if str(year) == str(year_count):
                count += 1
                dates.update({year_count: count})
        year_count += 1
    dates.update({0: bdate_missing})

    logger.info('Total amount of users: ' + str(total_amount_of_participants))

    female_ptg = float(female / total_amount_of_participants) * 100
    logger.info('Female percentage of users: ' + str(female_ptg))

    male_ptg = float(male / total_amount_of_participants) * 100
    logger.info('Male percentage of users: ' + str(male_ptg))

    inactive_month_ptg = float(inactive_month / total_amount_of_participants) * 100
    logger.info('Inactive from last month percentage of users: ' + str(inactive_month_ptg))

    inactive_week_ptg = float(inactive_week / total_amount_of_participants) * 100
    logger.info('Inactive from last week percentage of users: ' + str(inactive_week_ptg))

    time_undefined_ptg = float(time_undefined / total_amount_of_participants) * 100
    logger.info('Can\'t determine activity of users in percentage: ' + str(time_undefined_ptg))

    active_users_ptg = float(active_time / total_amount_of_participants) * 100
    logger.info('Active users in percentage: ' + str(active_users_ptg))

    if not os.path.isdir(chart_folder):
        os.mkdir(chart_folder)

    # create a DataFrame with some data
    data = {'Members': ['Female', 'Male'], 'Count': [female_ptg, male_ptg]}
    df = pd.DataFrame(data)

    # plot the pie chart
    df.plot.pie(y='Count', labels=df['Members'], explode=(0.2, 0.2),
                autopct=lambda cent: '{:1.2f}%'.format(cent) if cent > 0 else '')

    # display the chart
    plt.gca().set_title('Gender of users')
    plt.gca().get_legend().remove()
    plt.gca().set_ylabel('')
    plt.savefig(chart_folder + '/users_by_gender.png')

    # BUG WITH DISPLAYING PERCENTAGE ON CHARTS

    # create a DataFrame with some data
    data = {'Members': ['Inactive \nlast month', 'Inactive \nlast week', "Can't get \nlast activity", 'Active \nusers'],
            'Count': [inactive_month_ptg, inactive_week_ptg, time_undefined_ptg, active_users_ptg]}
    df = pd.DataFrame(data)

    # plot the pie chart

    df.plot.pie(y='Count', labels=df['Members'], explode=(0.2, 0.2, 0.2, 0.2),
                autopct=lambda cent: '{:1.2f}%'.format(cent) if cent > 0 else '')

    # display the chart
    plt.gca().set_title('Users activity')
    plt.gca().get_legend().remove()
    plt.gca().set_ylabel('')
    plt.savefig(chart_folder + '/users_activity.png')

    years, amount_of_people, years_from_1990, amount_of_people_from_1990 = [], [], [], []
    for k, v in dates.items():
        years.append(int(k))
        amount_of_people.append(int(v))
        if k >= 1990:
            years_from_1990.append(int(k))
            amount_of_people_from_1990.append(int(v))
    data = {'Years': years, 'Counts': amount_of_people}

    df = pd.DataFrame(data)

    plt.figure(figsize=(25, 20))
    # create histogram chart
    hist_plot = sns.barplot(x='Years', y='Counts', data=df, width=1)
    for item in hist_plot.get_xticklabels():
        item.set_rotation(45)
    plt.savefig(chart_folder + '/whole_users_birth_date.png')

    data = {'Years': years_from_1990, 'Counts': amount_of_people_from_1990}

    df = pd.DataFrame(data)

    plt.figure(figsize=(25, 20))
    # create histogram chart
    hist_plot = sns.barplot(x='Years', y='Counts', data=df, width=1)
    for item in hist_plot.get_xticklabels():
        item.set_rotation(45)
    plt.savefig(chart_folder + '/users_with_birth_date_from_1990.png')


def community_posts_analyser(vk_session, time_shift, chart_folder):
    group = config.groups.group_to_check
    tools = vk_api.VkTools(vk_session)
    posts_info = {'date-views': [], 'date-likes': [], 'date-reposts': []}
    historical_posts_info = {'date-views': [], 'date-likes': [], 'date-reposts': []}
    logger.info('Starting analysis of the posts.')
    try:
        wall = tools.get_all('wall.get', 10, {'owner_id': -int(group)})
        logger.info('Total amount of the posts is ' + str(wall['count']))
        for post in range(wall['count']):
            if wall['items'][post]['date'] >= time_shift:
                posts_info['date-views'].append(
                    str(wall['items'][post]['date']) + ',' + str(wall['items'][post]['views']['count']))
                posts_info['date-likes'].append(
                    str(wall['items'][post]['date']) + ',' + str(wall['items'][post]['likes']['count']))
                posts_info['date-reposts'].append(
                    str(wall['items'][post]['date']) + ',' + str(wall['items'][post]['reposts']['count']))
            try:
                historical_posts_info['date-views'].append(
                    str(wall['items'][post]['date']) + ',' + str(wall['items'][post]['views']['count']))
                historical_posts_info['date-likes'].append(
                    str(wall['items'][post]['date']) + ',' + str(wall['items'][post]['likes']['count']))
                historical_posts_info['date-reposts'].append(
                    str(wall['items'][post]['date']) + ',' + str(wall['items'][post]['reposts']['count']))
            except KeyError as e:
                logger.error('Post ' + str(wall['items'][post]['date']) + ' have an error while processing: ' + str(e))
    except vk_api.exceptions.ApiError as e:
        logger.error('Error: ' + str(e))

    if not os.path.isdir(chart_folder):
        os.mkdir(chart_folder)

    date, views, likes, reposts = [], [], [], []
    for post in posts_info['date-views']:
        post = post.split(',')
        date.append(datetime.fromtimestamp(int(post[0]), tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S'))
        views.append(int(post[1]))
    for post in posts_info['date-likes']:
        post = post.split(',')
        likes.append(int(post[1]))
    for post in posts_info['date-reposts']:
        post = post.split(',')
        reposts.append(int(post[1]))
    date.reverse()
    views.reverse()
    likes.reverse()
    reposts.reverse()

    plot_creator(chart_folder, 'Post date', date, 'Views', views, 'Posts Views', '/posts_views.png')
    plot_creator(chart_folder, 'Post date', date, 'Likes', likes, 'Posts Likes', '/posts_likes.png')
    plot_creator(chart_folder, 'Post date', date, 'Reposts', reposts, 'Posts Reposts', '/posts_reposts.png')

    date, views, likes, reposts = [], [], [], []
    for post in historical_posts_info['date-views']:
        post = post.split(',')
        date.append(datetime.fromtimestamp(int(post[0]), tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S'))
        views.append(int(post[1]))
    for post in historical_posts_info['date-likes']:
        post = post.split(',')
        likes.append(int(post[1]))
    for post in historical_posts_info['date-reposts']:
        post = post.split(',')
        reposts.append(int(post[1]))
    date.reverse()
    views.reverse()
    likes.reverse()
    reposts.reverse()

    plot_creator(chart_folder, 'Post date', date, 'Views', views, 'Posts Views', '/historical_posts_views.png')
    plot_creator(chart_folder, 'Post date', date, 'Likes', likes, 'Posts Likes', '/historical_posts_likes.png')
    plot_creator(chart_folder, 'Post date', date, 'Reposts', reposts, 'Posts Reposts', '/historical_posts_reposts.png')


def plot_creator(chart_folder, x, x_data, y, y_data, title, plot_name):
    # create a DataFrame with some data
    data = {x: x_data, y: y_data}
    df = pd.DataFrame(data)
    df[x] = pd.to_datetime(df[x], format='%Y-%m-%d %H:%M:%S')
    # plot
    df.plot(x=x, y=y, figsize=(25, 20))
    # display the chart
    plt.gca().set_title(title)
    if 'historical' not in plot_name:
        plt.xticks(df[x], rotation=45)
        plt.yticks(df[y])
    else:
        plt.xticks(rotation=45)

    plt.savefig(chart_folder + plot_name)


if __name__ == '__main__':
    asyncio.run(main())
