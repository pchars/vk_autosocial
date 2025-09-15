from utils import AppConfig, get_logger
import vk_api
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

    async def friends_remover(self, time_shift: int) -> Dict[str, Any]:
        """
        Remove inactive and deactivated friends

        Returns:
            Dictionary with removal statistics
        """
        stats = {
            'deleted_count': 0,
            'problem_ids': [],
            'errors': []
        }

        try:
            response = await self.vk_client.get_friends(fields=["deactivated", "last_seen"])

            if not response:
                logger.warning("No friends found or failed to fetch friends list")
                return stats

            for user in response:
                try:
                    # Check if user should be removed
                    should_remove = (
                            user.get("deactivated") or
                            (user.get('last_seen') and user['last_seen']['time'] <= time_shift)
                    )

                    if not should_remove:
                        continue

                    # Try to remove friend
                    success = await self.vk_client.delete_friend(user_id=user["id"])

                    if success:
                        stats['deleted_count'] += 1
                        logger.debug(f"Removed friend: {user.get('id')}")
                    else:
                        stats['problem_ids'].append(user["id"])
                        logger.warning(f"Failed to remove friend: {user.get('id')}")

                except Exception as e:
                    error_msg = f"Error processing user {user.get('id')}: {e}"
                    stats['errors'].append(error_msg)
                    logger.error(error_msg)

            # Log results
            logger.info(f"Removed {stats['deleted_count']} inactive friends")

            if stats['problem_ids']:
                logger.warning(f"{len(stats['problem_ids'])} users couldn't be removed")
                logger.debug(f"Problem IDs: {stats['problem_ids']}")

            if stats['errors']:
                logger.error(f"Encountered {len(stats['errors'])} errors during processing")

            return stats

        except Exception as e:
            logger.error(f"Critical error in friends_remover: {e}")
            stats['errors'].append(str(e))
            return stats