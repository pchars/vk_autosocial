import math
import random
import imagehash
from PIL import Image, UnidentifiedImageError
import configparser
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import vk_api
import aiohttp
import asyncio
from typing import List, Dict, Optional
import cv2
from datetime import datetime
import os
import time
import requests
import numpy as np
from PIL import Image
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from skimage.color import rgb2lab, deltaE_ciede2000


class Colors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'


config = configparser.ConfigParser()
config.read('conf.cfg')


def main():
    folder = 'tmp'
    os.makedirs(folder, exist_ok=True)
    chart_folder = 'charts'
    month = int(time.time() - 2678400)  # current time minus 31 day
    week = int(time.time() - 604800)  # current time minus one week
    vk_session = session_maker()

    # Public Management
    #images_getter(folder, vk_session)
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    #asyncio.run(images_getter_async(
    #    folder=folder,
    #    vk_session=vk_session,
    #    max_posts_per_group=100,  # Set to None for no limit
    #    max_concurrent=10
    #))

    #check_for_duplicates(folder)
    # stories_publisher(folder, vk_session)

    post_publisher(folder, vk_session, time_delay=14400)
    #post_publisher(folder, vk_session)
    # wall_cleaner(vk_session)

    # Page Management
    # friends_list_cleaner(vk_session, week)
    # subscription_cleaner(vk_session)
    sex = 1  # gender for friends adder, 1 - female, 2 - male
    # friends_adder(vk_session, month, sex)  # Add friends from GROUPS variable by gender and activity

    # Communities analysing
    # community_members_analyser(vk_session, month, week, chart_folder)
    # community_posts_analyser(vk_session, week, chart_folder)


# TODO fix case when you're not member of the group "Error: [203] Access to group denied: access to the group is denied."
def friends_adder(vk_session, time_shift, sex):
    # Get the list of friend friends users
    api = vk_session.get_api()
    count = 0
    flood_count = 0
    # Iterate over the list of these users
    # TODO do the testing with these two groups GROUPS=214580081,188503062
    for group in config['GROUPS']['GROUPS'].split(','):
        try:
            response = api.groups.getMembers(group_id=group, fields='sex, last_seen')
            if len(response["items"]) != 0:
                for user in response["items"]:
                    # add users by gender and send invites only to active users
                    if user['sex'] == sex and ('last_seen' in user.keys() and user['last_seen']['time'] >= time_shift):
                        # Add the user to friends
                        api.friends.add(user_id=user["id"])
                        count += 1
            else:
                print('{0}[Warn]{1} No users found in the group.'.format(Colors.WARNING, Colors.ENDC))
        except vk_api.exceptions.ApiError as api_err:
            if "flood" in str(api_err).lower():
                print('{0}[Warn]{1} Limit for adding friends was reached.'.format(Colors.WARNING, Colors.ENDC))
                flood_count += 1
                if flood_count >= 3:
                    break
            elif "blacklist" in str(api_err).lower():
                print(Colors.FAIL + '[Error]' + Colors.ENDC + ' Unexpected error was occur. Error: ' + str(
                    api_err))
                continue
        if flood_count >= 3:
            break
        if count > 250:
            break
    print(Colors.OKGREEN + '[Info]' + Colors.ENDC + ' Requests to ' + str(count) + ' people(s) were sent.')


def friends_list_cleaner(vk_session, time_shift):
    # Get the list of blocked users
    api = vk_session.get_api()
    count_of_deleted_users = 0
    problem_ids_list = []

    try:
        response = api.friends.get(fields="deactivated, last_seen")
        # Iterate over the list of blocked users
        for user in response["items"]:
            if user.get("deactivated") or ('last_seen' in user.keys() and user.get("last_seen")['time'] <= time_shift):
                # Delete the user from friends
                try:
                    api.friends.delete(user_id=user["id"])
                    count_of_deleted_users += 1
                except vk_api.exceptions.ApiError as error:
                    if "No friend or friend request found" in str(error):
                        print(Colors.FAIL + '[Error]' + Colors.ENDC + ' Error occurred: ' + str(error))
                        problem_ids_list.append(user["id"])
                    else:
                        print(Colors.FAIL + '[Error]' + Colors.ENDC + ' Error occurred: ' + str(error))
        print('{0}[Info] {1}{2} deactivated and inactive friends were moved to subscribers.'.format(
            Colors.OKGREEN, Colors.ENDC, str(count_of_deleted_users)))
        if len(problem_ids_list) > 0:
            print('{0}[Info] {1}{2} problem user(s) was/were not deleted from friends list, find list below.'.format(
                Colors.OKGREEN, Colors.ENDC, str(len(problem_ids_list))))
            print('{0}[Info]{1} ID(s): {2}'.format(
                Colors.OKGREEN, Colors.ENDC, str(problem_ids_list)))
    except vk_api.exceptions.ApiError as error:
        if 'User authorization failed' in str(error):
            print(Colors.FAIL + '[Error]' + Colors.ENDC + ' Error occurred: ' + str(error))
            print(Colors.WARNING + '[Warn]' + Colors.ENDC + ' Trying to do re-auth.')
            vk_session.auth(reauth=True)
        else:
            print(Colors.FAIL + '[Error]' + Colors.ENDC + ' Error occurred: ' + str(error))


# TODO should be tested with values more than 1500
def subscription_cleaner(vk_session):
    # Get the list of blocked users
    api = vk_session.get_api()
    count_of_deleted_users = 0
    problem_ids_list = []
    try:
        response = api.friends.getRequests(out=1, offset=0, count=1)
        iter_count = int(math.ceil((float(response['count']) / 1000)))
        for count in range(0, iter_count + 1):
            response = api.friends.getRequests(out=1, offset=(iter_count - count) * 1000, count=1000)
            # Iterate over the list of blocked users
            for user in response["items"]:
                # Delete the user from friends
                try:
                    api.friends.delete(user_id=user)
                    count_of_deleted_users += 1
                except vk_api.exceptions.ApiError as error:
                    print(Colors.FAIL + '[Error]' + Colors.ENDC + ' Error occurred: ' + str(error))
        print('{0}[Info] {1}{2} subscriptions were deleted.'.format(
            Colors.OKGREEN, Colors.ENDC, str(count_of_deleted_users)))
        if len(problem_ids_list) > 0:
            print(
                '{0}[Info] {1}{2} problem user(s) was/were not deleted from subscription list, find list below.'.format(
                    Colors.OKGREEN, Colors.ENDC, str(len(problem_ids_list))))
            print('{0}[Info]{1} ID(s): {2}'.format(
                Colors.OKGREEN, Colors.ENDC, str(problem_ids_list)))
    except vk_api.exceptions.ApiError as error:
        if 'User authorization failed' in str(error):
            print(Colors.FAIL + '[Error]' + Colors.ENDC + ' Error occurred: ' + str(error))
            print(Colors.WARNING + '[Warn]' + Colors.ENDC + ' Trying to do re-auth.')
            vk_session.auth(reauth=True)
        else:
            print(Colors.FAIL + '[Error]' + Colors.ENDC + ' Error occurred: ' + str(error))


def session_maker():
    login, password, api_version = '', '', ''
    for key, value in config['AUTH'].items():
        if key.lower() == 'phone_number':
            login = value
        elif key.lower() == 'password':
            password = value
        elif key.lower() == 'api_version':
            api_version = value

    vk_session = vk_api.VkApi(login=login, password=password, api_version=api_version)
    vk_session.auth(token_only=True)
    try:
        try:
            vk_session.auth(token_only=True)
        except vk_api.AuthError as error_msg:
            print(Colors.FAIL + '[Error] Problem with Auth observed. Error: ' + Colors.ENDC + str(error_msg))
            exit()
    except requests.exceptions.ConnectionError as con_error_msg:
        print(Colors.FAIL + '[Error] Network problem observed. Error: ' + Colors.ENDC + str(con_error_msg))
        exit()
    print(Colors.OKGREEN + '[Info]' + Colors.ENDC + ' Connection was successfully established.')
    return vk_session


# def wall_cleaner(vk_session):
#     print(Colors.OKGREEN + '[Info]' + Colors.ENDC + ' Starting wall clean request.')
#     tools = vk_api.VkTools(vk_session)
#     vk = vk_session.get_api()
#     try:
#         wall = tools.get_all('wall.get', 100, {'owner_id': -int(config['GROUPS']['YOUR_GROUP'])})
#     except vk_api.exceptions as e:
#         print(Colors.FAIL + '[Error]' + Colors.ENDC + ' Error: ' + str(e))
#
#     print(Colors.OKGREEN + '[Info]' + Colors.ENDC + ' Posts count: ', wall['count'])
#     for post in range(wall['count']):
#         post_for_remove = vk.wall.delete(owner_id=-int(config['GROUPS']['YOUR_GROUP']), post_id=wall['items'][post]['id'])
#
#         print(Colors.OKGREEN + '[Info]' + Colors.ENDC + ' Post was removed: ' + str(post_for_remove))

def wall_cleaner(vk_session, delete_postponed: bool = True, delete_published: bool = True):
    """
    Удаляет посты из группы с возможностью выбора типа

    :param vk_session: Сессия VK API
    :param delete_postponed: Удалять отложенные посты (по умолчанию True)
    :param delete_published: Удалять опубликованные посты (по умолчанию True)
    """
    log_with_timestamp(Colors.OKGREEN, "Starting comprehensive wall cleanup")

    group_id = -int(config['GROUPS']['YOUR_GROUP'])
    vk = vk_session.get_api()
    total_deleted = 0

    def delete_posts(post_type: str):
        nonlocal total_deleted
        offset = 0
        count = 100  # Максимальное значение для одного запроса

        while True:
            try:
                # Получаем посты пачками
                response = vk.wall.get(
                    owner_id=group_id,
                    filter=post_type if post_type else 'all',
                    count=count,
                    offset=offset
                )

                if not response['items']:
                    break

                # Удаляем в обратном порядке (от новых к старым)
                for post in reversed(response['items']):
                    try:
                        result = vk.wall.delete(
                            owner_id=group_id,
                            post_id=post['id']
                        )
                        if result == 1:
                            log_with_timestamp(Colors.OKGREEN, f"Deleted post {post['id']}")
                            total_deleted += 1
                        else:
                            log_with_timestamp(Colors.FAIL, f"Failed to delete post {post['id']}")

                        # Задержка для избежания флуд-контроля
                        time.sleep(0.35)

                    except vk_api.ApiError as e:
                        if 'flood' in str(e).lower():
                            log_with_timestamp(Colors.WARNING, "Flood control triggered, pausing for 5 sec")
                            time.sleep(5)
                        else:
                            log_with_timestamp(Colors.FAIL, f"API Error: {str(e)}")
                            break

                offset += count

            except Exception as e:
                log_with_timestamp(Colors.FAIL, f"Error: {str(e)}")
                break

    try:
        # Удаляем отложенные посты
        if delete_postponed:
            log_with_timestamp(Colors.OKGREEN, "Processing postponed posts...")
            delete_posts(post_type='postponed')

        # Удаляем опубликованные посты
        if delete_published:
            log_with_timestamp(Colors.OKGREEN, "Processing published posts...")
            delete_posts(post_type='owner')

    except Exception as e:
        log_with_timestamp(Colors.FAIL, f"Critical error: {str(e)}")

    log_with_timestamp(Colors.OKGREEN, f"Cleanup completed. Total deleted: {total_deleted}")















def post_publisher(folder, vk_session, time_delay=28800):
    print(Colors.OKGREEN + '[Info]' + Colors.ENDC + ' Starting publication of posts.')
    group = config['GROUPS']['YOUR_GROUP']

    # Get an upload URL for a photo
    vk = vk_session.get_api()
    upload_url = vk.photos.getWallUploadServer(group_id=int(group))['upload_url']
    photos, image_counter = [], 1

    # Identify Delay timer
    delay = time_delay

    # Use time of last postponed post instead of NOW in case we have postponed posts
    dates = []
    p = vk.wall.get(owner_id=-int(group), filter='postponed')
    if p['count'] > 0:
        for item in p['items']:
            dates.append(item['date'])
        dates.sort()
        now = int(dates[-1]) + delay
        print(Colors.OKGREEN + '[Info]' + Colors.ENDC + ' Group already contains some postponed posts, first post will '
                                                        'be published at ' + str(now))
    else:
        # Set start time in seconds
        if config['POSTS']['START_TIME'] == '':
            now = time.time()
            print(
                '{0}[Info]{1} There is no postponed posts in the group and config is not configured, I\'m taking '
                'current time for first post: {2}'.format(
                    Colors.OKGREEN, Colors.ENDC, str(now)))
        else:
            now = float(config['POSTS']['START_TIME'])
            print(
                Colors.OKGREEN + '[Info]' + Colors.ENDC + ' There is no postponed posts in the group, I\'m taking time '
                                                          'from the config: ' + str(now))

    # Upload the photo to the server

    for file in os.listdir(folder):
        photo = open(folder + '/' + file, 'rb')
        response = requests.post(upload_url, files={'photo': photo}).json()

        # Save the photo to the server and get the photo ID
        try:
            vk_photo = vk.photos.saveWallPhoto(group_id=int(group), server=response['server'], photo=response['photo'],
                                               hash=response['hash'])[0]
        except vk_api.exceptions.ApiError as error_msg:
            if "flood" in str(error_msg).lower():
                print(Colors.WARNING + '[Warn]' + Colors.ENDC + ' Limit for adding postponed posts was reached.')
                break
            else:
                print(Colors.FAIL + '[Error]' + Colors.ENDC + ' Unexpected error was occur. Error: ' + str(error_msg))

        photo_id = f"photo{vk_photo['owner_id']}_{vk_photo['id']}"
        if len(photos) < 6:
            photos.append(photo_id)
        elif len(photos) == 6:
            post_maker(delay, group, now, photos, vk)
            delay += time_delay
        os.remove(folder + '/' + file)
    print(Colors.OKGREEN + '[Info]' + Colors.ENDC + ' Finished preparing postponed posts.')



def delete_duplicates_from_text_db(db_text_file=config['POSTS']['TEXT']):
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
    db_text = config['POSTS']['TEXT']
    quotes_for_post = delete_duplicates_from_text_db(db_text)
    try:
        p = vk.wall.get(owner_id=-int(group), filter='postponed')
        # Make a delayed post on the wall of a user or community
        if len(config['POSTS']['GROUP_CHAT_REMINDER_TEXT']) == 0 and len(config['POSTS']['HASHTAGS']) == 0:
            vk.wall.post(owner_id=-int(group), publish_date=publish_date, attachments=photos)
            print(Colors.OKGREEN + '[Info]' + Colors.ENDC + ' Post was published. Going to schedule next post.')
        else:
            if int(round((publish_date % time.time()) / 60 / 60 / 24, 0)) % int(
                    config['POSTS']['GROUP_CHAT_REMINDER']) == 0 and (p['count'] > 0 and (config['POSTS']['GROUP_CHAT_LINK'] not in p['items'][-1]['text'] and config['POSTS']['GROUP_CHAT_LINK'] not in p['items'][-2]['text'])):
                vk.wall.post(owner_id=-int(group),
                             message=str(config['POSTS']['GROUP_CHAT_REMINDER_TEXT']).format(
                                 config['POSTS']['GROUP_CHAT_LINK'], config['POSTS']['HASHTAGS'], '\n\n'),
                             publish_date=publish_date, attachments=photos)
                print(Colors.OKGREEN + '[Info] ' + Colors.ENDC + 'Post about CHAT was published. Going to schedule next'
                                                                 'post.')
            else:
                vk.wall.post(owner_id=-int(group),
                             message=random.choice(quotes_for_post) + '\n\n' + config['POSTS']['HASHTAGS'],
                             publish_date=publish_date, attachments=photos)
                print(Colors.OKGREEN + '[Info]' + Colors.ENDC + ' Post was published. Going to schedule next post.')
    except vk_api.exceptions.ApiError as e:
        print(Colors.FAIL + '[Error]' + Colors.ENDC + ' Error: ' + str(e) + ', publish_date is ' + str(publish_date))
        return
    photos.clear()


def log_with_timestamp(color, message):
    """Helper function to print logs with timestamps and colors."""
    print(f"{color}[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {Colors.ENDC}{message}")


async def download_image(session: aiohttp.ClientSession, url: str, path: str, retries: int = 10) -> bool:
    """Download image with retries on failure."""
    for attempt in range(retries):
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=120)) as resp:
                if resp.status == 200:
                    with open(path, 'wb') as f:
                        f.write(await resp.read())
                    log_with_timestamp(Colors.OKGREEN, f"[Info] Downloaded: {path}")
                    return True
                log_with_timestamp(Colors.WARNING, f"[Warn] HTTP {resp.status}: {url} (attempt {attempt + 1}/{retries})")
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            log_with_timestamp(Colors.FAIL, f"[Error] Failed: {url} - {type(e).__name__} (attempt {attempt + 1}/{retries})")
        await asyncio.sleep(1)
    return False


async def fetch_group_posts(
        vk,
        group_id: str,
        max_posts: Optional[int] = None,
        batch_size: int = 100
) -> List[Dict]:
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
        log_with_timestamp(Colors.FAIL, f"[Error] Failed to get wall length: {e}")
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
            log_with_timestamp(
                Colors.OKGREEN,
                f"[Info] Group {group_id}: loaded {len(all_posts)}/{post_limit} posts"
            )

            await asyncio.sleep(0.5)  # Rate limiting

        except Exception as e:
            log_with_timestamp(Colors.FAIL, f"[Error] Error loading posts (offset={offset}): {e}")
            break

    return all_posts


async def images_getter_async(
        folder: str,
        vk_session,
        max_posts_per_group: Optional[int] = None,
        max_concurrent: int = 10
) -> None:
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
        for group in config['GROUPS']['GROUPS'].split(','):
            group = group.strip()
            if not group:
                continue

            log_with_timestamp(Colors.OKGREEN, f"[Info] Processing group {group}...")
            posts = await fetch_group_posts(vk, group, max_posts=max_posts_per_group)

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
            log_with_timestamp(
                Colors.OKGREEN if success == len(urls) else Colors.WARNING,
                f"[Info] Group {group}: {success}/{len(urls)} images downloaded"
            )


def check_for_duplicates(folder: str, similarity_threshold: int = 5) -> None:
    """
    Remove broken images and duplicates using perceptual hashing with parallel processing.

    Args:
        folder: Path to directory with images
        similarity_threshold: Max hash difference to consider images identical (0=exact match)
    """
    if not os.path.exists(folder):
        log_with_timestamp(Colors.FAIL, f"Directory does not exist: {folder}")
        return

    hashes = {}
    broken_files = []
    duplicate_files = []

    def process_file(file: str):
        filepath = os.path.join(folder, file)
        try:
            with Image.open(filepath) as img:
                if img.mode in ('RGBA', 'LA'):
                    img = img.convert('RGB')
                return file, imagehash.phash(img)
        except (OSError, IOError, UnidentifiedImageError, ValueError) as e:
            log_with_timestamp(Colors.WARNING, f"[Warn] Invalid image detected: {file} - {str(e)[:50]}...")
            return file, None
        except Exception as e:
            log_with_timestamp(Colors.FAIL, f"[Error] Unexpected error processing {file}: {str(e)[:50]}...")
            return file, None

    try:
        # First pass: parallel processing of all files
        with ThreadPoolExecutor(max_workers=min(32, os.cpu_count() * 2 + 4)) as executor:
            results = list(executor.map(process_file, os.listdir(folder)))

        # Remove broken files
        for file, _ in filter(lambda x: x[1] is None, results):
            try:
                os.remove(os.path.join(folder, file))
                log_with_timestamp(Colors.WARNING, f"[Warn] Deleted invalid image: {file}")
                broken_files.append(file)
            except Exception as e:
                log_with_timestamp(Colors.FAIL, f"[Error] Failed to delete {file}: {str(e)[:50]}...")

        # Second pass: duplicate detection with threshold
        hash_dict = {}
        for file, img_hash in filter(lambda x: x[1] is not None, results):
            is_duplicate = False
            for existing_hash in hash_dict:
                if img_hash - existing_hash <= similarity_threshold:
                    duplicate_files.append(file)
                    is_duplicate = True
                    break
            if not is_duplicate:
                hash_dict[img_hash] = file

        # Remove duplicates
        for file in duplicate_files:
            try:
                os.remove(os.path.join(folder, file))
                log_with_timestamp(Colors.WARNING, f"[Warn] Deleted duplicate: {file}")
            except Exception as e:
                log_with_timestamp(Colors.FAIL, f"[Error] Failed to delete duplicate {file}: {str(e)[:50]}...")

        # Summary log
        log_with_timestamp(
            Colors.OKGREEN,
            f"[Info] Cleanup completed. Removed {len(broken_files)} invalid and {len(duplicate_files)} duplicate images."
        )

    except Exception as e:
        log_with_timestamp(Colors.FAIL, f"[Error] Fatal error during duplicate check: {str(e)}")





def stories_publisher(folder, vk_session):
    counter = 0
    # Upload the photo to the server
    for file in os.listdir(folder):
        image = Image.open(folder + '/' + file)

        # Get the size of the image
        width, height = image.size
        if height == 1080 and width <= 721:
            photo = open(folder + '/' + file, 'rb')
            group = config['GROUPS']['YOUR_GROUP']
            # Get an upload URL for a photo
            vk = vk_session.get_api()
            upload_server = vk.stories.getPhotoUploadServer(group_id=int(group), add_to_news=1)['upload_url']
            # Upload the story
            response = requests.post(upload_server, files={'file': photo}).json()
            # Save the story
            try:
                vk.stories.save(upload_results=response['response']['upload_result'])
            except vk_api.exceptions.ApiError as e:
                print(Colors.FAIL + '[Error] ' + Colors.ENDC + str(e))

            os.remove(folder + '/' + file)
            print(Colors.OKGREEN + '[Info]' + Colors.ENDC + ' Story was published successfully.')
            counter += 1
            if counter == 10:
                break


def community_members_analyser(vk_session, month, week, chart_folder):
    group = config['GROUPS']['GROUP_TO_CHECK']
    vk = vk_session.get_api()
    try:
        vk.groups.join(group_id=group)
    except vk_api.exceptions.ApiError:
        print(Colors.WARNING + '[Warn]' + Colors.ENDC + ' Script tried to join community. You already part of the '
                                                        'group or public.')
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

    print(Colors.OKGREEN + '[Info]' + Colors.ENDC + ' Total amount of users: ' + str(total_amount_of_participants))

    female_ptg = float(female / total_amount_of_participants) * 100
    print(Colors.OKGREEN + '[Info]' + Colors.ENDC + ' Female percentage of users: ' + str(female_ptg))

    male_ptg = float(male / total_amount_of_participants) * 100
    print(Colors.OKGREEN + '[Info]' + Colors.ENDC + ' Male percentage of users: ' + str(male_ptg))

    inactive_month_ptg = float(inactive_month / total_amount_of_participants) * 100
    print('{0}[Info]{1} Inactive from last month percentage of users: {2}'.format(Colors.OKGREEN, Colors.ENDC,
                                                                                  str(inactive_month_ptg)))

    inactive_week_ptg = float(inactive_week / total_amount_of_participants) * 100
    print('{0}[Info]{1} Inactive from last week percentage of users: {2}'.format(Colors.OKGREEN, Colors.ENDC,
                                                                                 str(inactive_week_ptg)))

    time_undefined_ptg = float(time_undefined / total_amount_of_participants) * 100
    print("{0}[Info]{1} Can't determine activity of users in percentage: {2}".format(Colors.OKGREEN, Colors.ENDC,
                                                                                     str(time_undefined_ptg)))

    active_users_ptg = float(active_time / total_amount_of_participants) * 100
    print(Colors.OKGREEN + '[Info]' + Colors.ENDC + ' Active users in percentage: ' + str(active_users_ptg))

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
    group = config['GROUPS']['GROUP_TO_CHECK']
    tools = vk_api.VkTools(vk_session)
    posts_info = {'date-views': [], 'date-likes': [], 'date-reposts': []}
    historical_posts_info = {'date-views': [], 'date-likes': [], 'date-reposts': []}
    print(Colors.OKGREEN + '[Info]' + Colors.ENDC + ' Starting analysis of the posts.')
    try:
        wall = tools.get_all('wall.get', 10, {'owner_id': -int(group)})
        print(Colors.OKGREEN + '[Info]' + Colors.ENDC + ' Total amount of the posts is ' + str(wall['count']))
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
                print('{0}[Error]{1} Post {2} have an error while processing: {3}'.format(Colors.FAIL, Colors.ENDC, str(
                    wall['items'][post]['date']), str(e)))
    except vk_api.exceptions.ApiError as e:
        print(Colors.FAIL + '[Error]' + Colors.ENDC + ' Error: ' + str(e))

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
