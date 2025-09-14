from utils import AppConfig, get_logger

logger = get_logger(__name__)
config = AppConfig.from_cfg_file()


class PersonalPageManager:
    def __init__(self, vk_client):
        self.vk_client = vk_client

    async def friends_adder(self, time_shift, sex):
        # Get the list of friend friends users
        logger.info(f"Started friends invitation activity")
        count = 0
        flood_count = 0

        for group in config.groups.groups:
            try:
                response = await self.vk_client.get_group_members(
                    group_id=group,
                    fields=['sex', 'last_seen']
                )

                if not response:
                    logger.warning(f'No users found in group {group}.')
                    continue

                for user in response:
                    if (user.get('sex') == sex and
                            'last_seen' in user and
                            user['last_seen']['time'] >= time_shift):

                        success = await self.vk_client.add_friend(user_id=user['id'])
                        if success:
                            count += 1
                        else:
                            logger.warning(f"Failed to add user {user['id']}")
                            flood_count += 1
                        if flood_count >= 5:
                            logger.error(
                                f"Too much errors while adding users. Stopping this activity. Bad attempts: {flood_count}")
                            break

            except Exception as e:
                logger.error(f"Error processing group {group}: {e}")
                continue


            if count > 250:
                break

        logger.info(f'Friend requests sent to {count} people.')