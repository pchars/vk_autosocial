import asyncio
import math
import os
import random
import time
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional

import aiohttp
import matplotlib.pyplot as plt
import pandas as pd
import requests
import seaborn as sns
import vk_api
from PIL import Image

from api import VKAuth, VKClient
from services import ImageProcessor, PersonalPageManager, ContentManager
from utils import AppConfig, setup_logging, get_logger, OSManagement

config = AppConfig.from_cfg_file()
setup_logging(
    log_level=config.logging.log_level,
    log_file=config.logging.log_file,
    console_output=config.logging.console_output
)
logger = get_logger(__name__)
logger.info("Application started successfully!")


def main():
    try:
        folder_name = Path('images')
        folder = OSManagement(folder_path=folder_name)
        if folder.is_folder_exists():
            logger.info(f"Folder {folder_name} is ready to use")

        chart_folder = 'charts'
        # Constants
        week = int(time.time() - 604800)  # current time minus one week
        month = int(time.time() - 2678400)  # 31 день назад
        sex = 1  # gender for friends adder, 1 - female, 2 - male

        vk_auth = VKAuth(
            phone_number=config.auth.phone_number,
            password=config.auth.password,
            api_version=config.auth.api_version
        )

        vk_session = vk_auth.session_maker()
        vk_client = VKClient(vk_session)
        pp_manager = PersonalPageManager(vk_client)
        c_manager = ContentManager(vk_client)

        # Public Management
        image_processing = ImageProcessor(similarity_threshold=5, folder_path=folder_name)
        image_processing.check_for_duplicates(folder_name)
        logger.info("Image processing for duplicates was completed")
        # Wall cleaning
        wall_cleanup_stats = asyncio.run(
            c_manager.wall_cleaner(group_id=-config.groups.your_group, delete_postponed=True, delete_published=True))
        logger.info(f"Wall cleanup completed: {wall_cleanup_stats['deleted_count']} removed")

        # Personal Page Management
        # Friends cleaning
        friends_removal_stats = asyncio.run(pp_manager.friends_remover(month))
        logger.info(f"Friend cleanup completed: {friends_removal_stats['deleted_count']} removed")
        # Friends adder
        asyncio.run(pp_manager.friends_adder(month, sex))  # Add friends from GROUPS variable by gender and activity
        logger.info(f"Friend adding completed")
        # Friends Requests cleaning
        # 1 - requests which you sent to people, 0 - requests which people sent to you (your subscribers)
        friends_requests_removal_stats = asyncio.run(pp_manager.friends_requests_remover(1))
        logger.info(f"Friends requests cleanup completed: {friends_requests_removal_stats['deleted_count']} removed")
    except Exception as e:
        logger.error(f"Main execution failed: {e}")
        return

    # Public Management
    # if os.name == 'nt':
    #     asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    # asyncio.run(images_getter_async(
    #     folder=str(folder_name),
    #     vk_session=vk_session,
    #     max_posts_per_group=100,  # Set to None for no limit
    #     max_concurrent=10
    # ))

    # stories_publisher(folder_name, vk_session)

    # post_publisher(str(folder_name), vk_session, time_delay=14400)

    # Communities analysing
    # community_members_analyser(vk_session, month, week, chart_folder)
    # community_posts_analyser(vk_session, week, chart_folder)


def post_publisher(folder, vk_session, time_delay=28800):
    logger.info('Starting publication of posts.')
    group = config.groups.your_group
    vk = vk_session.get_api()

    # Getting upload_url one time for further usage
    upload_url = vk.photos.getWallUploadServer(group_id=int(group))['upload_url']
    # HTTP Session establishing for the POST requests
    session = requests.Session()

    photos, image_counter = [], 1
    delay = time_delay

    # Determining time for the postponed posts
    try:
        postponed = vk.wall.get(owner_id=-int(group), filter='postponed')
        if postponed['count'] > 0:
            dates = sorted(item['date'] for item in postponed['items'])
            now = int(dates[-1]) + delay
            logger.info(f'Using last postponed post time: {now}')
        else:
            now = float(config.posts.start_time) if config.posts.start_time else time.time()
            logger.info(f'Using config or current time: {now}')
    except Exception as e:
        logger.error(f'Failed to fetch postponed posts: {e}')
        now = time.time()

    # Uploading and publishing images
    for file_name in os.listdir(folder):
        file_path = os.path.join(folder, file_name)
        try:
            with open(file_path, 'rb') as photo:
                response = session.post(upload_url, files={'photo': photo}).json()
        except Exception as e:
            logger.error(f'Failed to upload {file_name}: {e}')
            continue

        try:
            vk_photo = vk.photos.saveWallPhoto(
                group_id=int(group),
                server=response['server'],
                photo=response['photo'],
                hash=response['hash']
            )[0]
        except vk_api.exceptions.ApiError as error_msg:
            if "flood" in str(error_msg).lower():
                logger.warning('Flood control triggered. Stopping.')
                break
            else:
                logger.error(f'VK API error: {error_msg}')
                continue

        photo_id = f"photo{vk_photo['owner_id']}_{vk_photo['id']}"
        photos.append(photo_id)

        if len(photos) == 6:
            post_maker(delay, group, now, photos, vk)
            delay += time_delay
            photos.clear()

        os.remove(file_path)

    logger.info('Finished preparing postponed posts.')


def delete_duplicates_from_text_db(db_text_file=config.posts.text):
    real_lines = []
    with open(db_text_file) as f:
        lines = f.readlines()
        for line in lines:
            if "\"" in line:
                line = line.replace('"', '')
            if "    " in line:
                line = line.replace("    ", "")
            real_lines.append(line)

        real_lines = list(set(real_lines))
    return real_lines


def post_maker(delay, group, start_time, photos, vk):
    # Calculate the publishing date as a Unix timestamp
    publish_date = int(start_time + delay)
    db_text = config.posts.text
    quotes_for_post = delete_duplicates_from_text_db(db_text)
    try:
        p = vk.wall.get(owner_id=-int(group), filter='postponed')
        # Make a delayed post on the wall of a user or community
        if len(config.posts.group_chat_reminder_text) == 0 and len(config.posts.hashtags) == 0:
            vk.wall.post(owner_id=-int(group), publish_date=publish_date, attachments=photos)
            logger.info('Post was published. Going to schedule next post.')
        else:
            if int(round((publish_date % time.time()) / 60 / 60 / 24, 0)) % int(
                    config.posts.group_chat_reminder) == 0 and (p['count'] > 0 and (
                    config.posts.group_chat_link not in p['items'][-1]['text'] and config.posts.group_chat_link not in
                    p['items'][-2]['text'])):
                vk.wall.post(owner_id=-int(group),
                             message=str(config.posts.group_chat_reminder_text).format(
                                 config.posts.group_chat_link, config.posts.hashtags, '\n\n'),
                             publish_date=publish_date, attachments=photos, primary_attachments_mode='grid')
                logger.info('Post about CHAT was published. Going to schedule next post.')
            else:
                vk.wall.post(owner_id=-int(group),
                             message=random.choice(quotes_for_post) + '\n\n' + config.posts.hashtags,
                             publish_date=publish_date, attachments=photos, primary_attachments_mode='grid')
                logger.info('Post was published. Going to schedule next post.')
    except vk_api.exceptions.ApiError as e:
        logger.error('Error: ' + str(e) + ', publish_date is ' + str(publish_date))
        return
    photos.clear()


async def download_image(session: aiohttp.ClientSession, url: str, path: str, retries: int = 10) -> bool:
    """Download image with retries on failure."""
    for attempt in range(retries):
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=120)) as resp:
                if resp.status == 200:
                    with open(path, 'wb') as f:
                        f.write(await resp.read())
                    logger.info(f"Downloaded: {path}")
                    return True
                logger.warning(f"HTTP {resp.status}: {url} (attempt {attempt + 1}/{retries})")
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.error(f"Failed: {url} - {type(e).__name__} (attempt {attempt + 1}/{retries})")
        await asyncio.sleep(1)
    return False


async def fetch_group_posts(vk, group_id: str, max_posts: Optional[int] = None, batch_size: int = 100) -> List[Dict]:
    """
    Fetch posts from VK group with pagination.

    Args:
        vk: Authenticated VK API instance
        group_id: Group ID or screen name
        max_posts: Maximum posts to fetch (None for all available)
        batch_size: Number of posts per API request (max 100)
    """
    try:
        # Get total post count
        total_count = (await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: vk.wall.get(owner_id=-int(group_id), count=1)
        ))['count']
    except Exception as e:
        logger.error(f"Failed to get wall length: {e}")
        return []

    post_limit = min(max_posts, total_count) if max_posts else total_count
    if post_limit <= 0:
        return []

    all_posts = []
    offset = 0

    while offset < post_limit:
        try:
            current_batch = min(batch_size, post_limit - offset)
            posts = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: vk.wall.get(
                    owner_id=-int(group_id),
                    count=current_batch,
                    offset=offset
                )
            )

            if not posts.get('items'):
                break

            all_posts.extend(posts['items'])
            offset += len(posts['items'])
            logger.info(f"Group {group_id}: loaded {len(all_posts)}/{post_limit} posts")

            await asyncio.sleep(0.5)  # Rate limiting

        except Exception as e:
            logger.error(f"Error loading posts (offset={offset}): {e}")
            break

    return all_posts


async def images_getter_async(folder: str, vk_session, max_posts_per_group: Optional[int] = None,
                              max_concurrent: int = 10) -> None:
    """
    Main downloader function with configurable limits.

    Args:
        folder: Target directory for images
        vk_session: Authenticated VK session
        max_posts_per_group: Max posts to process per group (None for all)
        max_concurrent: Maximum concurrent downloads
    """
    if not os.path.exists(folder):
        os.makedirs(folder)

    counter = 1
    vk = vk_session.get_api()
    connector = aiohttp.TCPConnector(limit=max_concurrent)
    timeout = aiohttp.ClientTimeout(total=60)

    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        for group in config.groups.groups:
            logger.info(f"Processing group {group}...")
            posts = await fetch_group_posts(vk, str(group), max_posts=max_posts_per_group)

            if not posts:
                continue

            # Extract all image URLs
            urls = []
            for post in posts:
                if post.get('marked_as_ads', 0) == 1:
                    continue
                for att in post.get('attachments', []):
                    if att.get('type') == 'photo' and 'sizes' in att.get('photo', {}):
                        urls.append(max(att['photo']['sizes'], key=lambda x: x['width'])['url'])

            # Download images
            tasks = []
            for url in urls:
                path = os.path.join(folder, f"img_{counter}.jpg")
                tasks.append(download_image(session, url, path))
                counter += 1

            results = await asyncio.gather(*tasks)
            success = sum(results)
            if success == len(urls):
                logger.info(f"Group {group}: {success}/{len(urls)} images downloaded")
            else:
                logger.warning(f"Group {group}: {success}/{len(urls)} images downloaded")


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
        date.append(datetime.utcfromtimestamp(int(post[0])).strftime('%Y-%m-%d %H:%M:%S'))
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
        date.append(datetime.utcfromtimestamp(int(post[0])).strftime('%Y-%m-%d %H:%M:%S'))
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
    main()
