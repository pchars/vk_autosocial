import math
import os
import random
import time
from datetime import datetime
import imagehash
from PIL import Image, UnidentifiedImageError
import vk_api
import requests
import configparser
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns


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
    chart_folder = 'charts'
    month = int(time.time() - 2678400)  # current time minus 31 day
    week = int(time.time() - 604800)  # current time minus one week
    vk_session = session_maker()

    # Public Management
    # images_getter(folder, vk_session)
    # check_for_duplicates(folder)
    # stories_publisher(folder, vk_session)
    # post_publisher(folder, vk_session)

    # Page Management
    # friends_list_cleaner(vk_session, week)
    # subscription_cleaner(vk_session)
    sex = 1  # gender for friends adder, 1 - female, 2 - male
    # friends_adder(vk_session, month, sex)  # Add friends from GROUPS variable by gender and activity

    # Communities analysing
    # community_members_analyser(vk_session, month, week, chart_folder)
    community_posts_analyser(vk_session, week, chart_folder)


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


# TODO to fix issue with "Error: [100] One of the parameters specified was missing or invalid: invalid publish_date param, publish_date is 1729260000", need to fix return from post_maker function
def post_publisher(folder, vk_session, time_delay=28800):
    print(Colors.OKGREEN + '[Info]' + Colors.ENDC + ' Starting publication of posts.')
    group = config['GROUPS']['YOUR_GROUP']
    # Set the delay in seconds
    if config['POSTS']['START_TIME'] == '':
        now = time.time()
    else:
        now = float(config['POSTS']['START_TIME'])
    delay = time_delay
    # Get an upload URL for a photo
    vk = vk_session.get_api()
    upload_url = vk.photos.getWallUploadServer(group_id=int(group))['upload_url']
    photos, image_counter = [], 1

    # Use time of last postponed post instead of NOW in case we have postponed posts
    dates = []
    # post_ids = []
    # messages = []
    p = vk.wall.get(owner_id=-int(group), filter='postponed')
    if p['count'] > 0:
        iter_count = int(math.ceil((float(p['count']) / 20)))
        for count in range(0, iter_count):
            p = vk.wall.get(owner_id=-int(group), filter='postponed', offset=count * 20)
            for post in p['items']:
                if 'post_type' in post.keys() and post['post_type'] == 'postpone':
                    if 'date' in post.keys():
                        dates.append(post['date'])
                    # post_ids.append(post['id'])
                    # messages.append(post['text'])
        dates.sort()
    # TODO: to make a feature which will allow rebuild GAP in time of postponed posts
    #     post_ids.sort()
    #     gap_detector = False
    #     for i in range(0, len(dates)):
    #         try:
    #             if dates[i] - dates[i+1] != time_delay:
    #                 print(Colors.WARNING + '[Warn]' + Colors.ENDC + ' There is a gap in postponed posts. Date rebuild is required!')
    #                 gap_detector = True
    #                 break
    #         except IndexError:
    #             continue
    #     if gap_detector:
    #         try:
    #             for post_id in post_ids:
    #                 i = 0
    #                 publish_date = int(now + delay)
    #                 # Make a delayed post on the wall of a user or community
    #                 vk.wall.edit(owner_id=-int(group),
    #                              post_id=post_id,
    #                              message=messages[i],
    #                              publish_date=publish_date)
    #                 i += 1
    #         except vk_api.exceptions.ApiError as e:
    #             print(Colors.FAIL + '[Error]' + Colors.ENDC + ' Error: ' + str(e))
    #             return
    #
    # exit()

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
            else:
                print(Colors.FAIL + '[Error]' + Colors.ENDC + ' Unexpected error was occur. Error:' + str(error_msg))
            break

        photo_id = f"photo{vk_photo['owner_id']}_{vk_photo['id']}"
        if len(photos) < 6:
            photos.append(photo_id)
        elif len(photos) == 6:
            if p['count'] > 0:
                post_maker(delay, group, now, photos, vk)
                # post_maker(delay, group, int(dates[-1]), photos, vk)
            else:
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
        # Make a delayed post on the wall of a user or community
        if int(round((publish_date % time.time()) / 60 / 60 / 24, 0)) % int(config['POSTS']['TEXT']) == 0:
            vk.wall.post(owner_id=-int(group),
                         message="Привет, дорогие участники! \n\nРады приветствовать вас в нашем уютном "
                                 "чате! Здесь мы сможем делиться новостями, устраивать обсуждения, поддерживать друг "
                                 "друга и просто весело проводить время. \n\nНе стесняйтесь знакомиться и "
                                 "делиться своими увлечениями! Будем рады видеть ваши фото, идеи, советы и всё, "
                                 "что может сделать наше сообщество ещё ярче! \n\nПрисоединяйтесь по "
                                 "ссылке: {0}\n\nДавайте сделаем этот чат удивительным местом! \n\n#cute "
                                 "#aesthetics #состояние_души #эстетика #вдохновение".format(
                             config['POSTS']['GROUP_CHAT_LINK']),
                         publish_date=publish_date, attachments=photos)
            print(Colors.OKGREEN + '[Info]' + Colors.ENDC + 'Post about CHAT was published. Going to schedule next '
                                                            'post.')
        else:
            vk.wall.post(owner_id=-int(group),
                         message=random.choice(quotes_for_post) + "\n\n#cute #aesthetics #состояние_души #эстетика "
                                                                  "#вдохновение",
                         publish_date=publish_date, attachments=photos)
            print(Colors.OKGREEN + '[Info]' + Colors.ENDC + ' Post was published. Going to schedule next post.')
    except vk_api.exceptions.ApiError as e:
        print(Colors.FAIL + '[Error]' + Colors.ENDC + ' Error: ' + str(e) + ', publish_date is ' + str(publish_date))
        return
    photos.clear()

def images_getter(folder, vk_session):
    if not os.path.isdir(folder):
        counter_for_image_name = 1
    else:
        counter_for_image_name = len(os.listdir(folder))
    for group in config['GROUPS']['GROUPS'].split(','):
        tools = vk_api.VkTools(vk_session)
        try:
            wall = tools.get_all('wall.get', 100, {'owner_id': -int(group)})
        except vk_api.exceptions.ApiError as e:
            print(Colors.FAIL + '[Error]' + Colors.ENDC + ' Error: ' + str(e))
            continue
        except vk_api.exceptions.ApiHttpError as e:
            print(Colors.FAIL + '[Error]' + Colors.ENDC + ' Error: ' + str(e))
            continue
        print(Colors.OKGREEN + '[Info]' + Colors.ENDC + ' Posts count: ', wall['count'])
        for post in range(wall['count']):
            if len(wall['items'][post]['attachments']) > 0 and wall['items'][post]['marked_as_ads'] == 0 and \
                    'is_pinned' not in wall['items'][post].keys() and 'copy_history' not in wall['items'][post].keys():
                for attachment in wall['items'][post]['attachments']:
                    if attachment['type'] == 'photo':
                        try:
                            image = requests.get(attachment['photo']['sizes'][-1]['url'])
                            if not os.path.isdir(folder):
                                os.mkdir(folder)
                            open(folder + "/image" + str(counter_for_image_name) + ".jpg", "wb").write(image.content)
                            counter_for_image_name += 1
                        except requests.exceptions.ConnectionError as e:
                            print(Colors.FAIL + '[Error] ' + Colors.ENDC + str(e))
    print(Colors.OKGREEN + '[Info]' + Colors.ENDC + ' All images were saved.')


def check_for_duplicates(folder):
    # Create a dictionary to store the hashes
    hashes = {}

    # Loop through all the files in the folder and remove broken images
    for file in os.listdir(folder):
        # Calculate the hash for the image
        try:
            image_hash = imagehash.phash(Image.open(folder + '/' + file))
        except UnidentifiedImageError:
            os.remove(folder + '/' + file)
            print(Colors.WARNING + '[Warn]' + Colors.ENDC + f' Image is broken and deleted: {file}')
        except OSError:
            os.remove(folder + '/' + file)
            print(Colors.WARNING + '[Warn]' + Colors.ENDC + f' Image is broken and deleted: {file}')

    # Loop through all the files in the folder and remove duplicates afterwards
    for file in os.listdir(folder):
        # Calculate the hash for the image
        image_hash = imagehash.phash(Image.open(folder + '/' + file))

        # If the hash is already in the dictionary, it is a duplicate
        if image_hash in hashes:
            os.remove(folder + '/' + file)
            print(Colors.WARNING + '[Warn]' + Colors.ENDC + f' Duplicate found and deleted: {file}')
        else:
            # Add the hash to the dictionary
            hashes[image_hash] = file
    print(Colors.OKGREEN + '[Info]' + Colors.ENDC + ' All duplicates were deleted.')


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