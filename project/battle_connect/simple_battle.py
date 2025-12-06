import json
import logging
import os
import asyncio
from datetime import datetime
from battle_connect.websocket_client import PSWebsocketClient
from battle_connect.config import GbolshnikConfig, SaveReplay

logger = logging.getLogger(__name__)


async def cleanup_old_replay_links():
    replay_dir = GbolshnikConfig.replay_log_dir
    if not os.path.exists(replay_dir):
        return
    
    try:
        # Find all replay link files
        replay_link_files = [
            f for f in os.listdir(replay_dir)
            if "_replay_link_" in f and f.endswith(".txt")
        ]
        
        if replay_link_files:
            deleted_count = 0
            for filename in replay_link_files:
                filepath = os.path.join(replay_dir, filename)
                try:
                    os.remove(filepath)
                    deleted_count += 1
                except OSError as e:
                    logger.warning(f"Failed to delete old replay link {filename}: {e}")
            
            if deleted_count > 0:
                logger.info(f"Cleaned up {deleted_count} old replay link file(s)")
    except Exception as e:
        logger.warning(f"Error cleaning up old replay links: {e}")


def cleanup_all_replay_files():
    replay_dir = GbolshnikConfig.replay_log_dir
    if not os.path.exists(replay_dir):
        return
    
    try:
        deleted_count = 0
        for filename in os.listdir(replay_dir):
            filepath = os.path.join(replay_dir, filename)
            try:
                if os.path.isfile(filepath):
                    os.remove(filepath)
                    deleted_count += 1
            except OSError as e:
                logger.warning(f"Failed to delete replay file {filename}: {e}")
        
        if deleted_count > 0:
            logger.info(f"Cleaned up {deleted_count} replay file(s) on exit")
    except Exception as e:
        logger.warning(f"Error cleaning up replay files: {e}")


async def save_replay_link(battle_tag):
    if not GbolshnikConfig.save_replay == SaveReplay.Always:
        logger.debug("Replay saving is disabled, skipping replay link save")
        return
    
    # Create replay directory if it doesn't exist
    replay_dir = GbolshnikConfig.replay_log_dir
    if not os.path.exists(replay_dir):
        os.makedirs(replay_dir)
        logger.debug(f"Created replay directory: {replay_dir}")
    
    # Create filename with battle tag and timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = os.path.join(replay_dir, f"{battle_tag}_replay_link_{timestamp}.txt")
    
    replay_link = battle_tag
    
    try:
        logger.debug(f"Attempting to save replay link to {filename}")
        with open(filename, 'w') as f:
            f.write(replay_link)
            f.flush()  # Force write to disk immediately
            try:
                os.fsync(f.fileno())  # Ensure it's written to disk
            except (AttributeError, OSError):
                pass
        logger.info(f"✓ Saved replay link: {replay_link} to {filename}")
    except Exception as e:
        logger.error(f"Failed to save replay link: {e}", exc_info=True)


async def get_ai_command(replay_link, request_json=None, player_id=None):
    from battle_connect.ai_decision import get_ai_command_from_replay
    
    import asyncio
    loop = asyncio.get_event_loop()
    command = await loop.run_in_executor(
        None,
        lambda: get_ai_command_from_replay(replay_link, request_json, player_id=player_id)
    )
    logger.info(f"Command from replay: {command}")
    return command


async def get_battle_tag_and_opponent(ps_websocket_client: PSWebsocketClient):
    while True:
        msg = await ps_websocket_client.receive_message()
        split_msg = msg.split("|")
        first_msg = split_msg[0]
        if "battle" in first_msg:
            battle_tag = first_msg.replace(">", "").strip()
            user_name = split_msg[-1].replace("☆", "").strip()
            opponent_name = (
                split_msg[4].replace(user_name, "").replace("vs.", "").strip()
            )
            return battle_tag, opponent_name


async def wait_for_request(ps_websocket_client: PSWebsocketClient):
    while True:
        msg = await ps_websocket_client.receive_message()
        # Request can come in format: >battle-tag\n|request|{json}
        # or just: |request|{json}
        lines = msg.split("\n")
        for line in lines:
            msg_split = line.split("|")
            if len(msg_split) > 2 and msg_split[1].strip() == "request" and msg_split[2].strip():
                try:
                    # Try to parse JSON - might have quotes or not
                    json_str = msg_split[2].strip()
                    # Remove surrounding quotes if present
                    if json_str.startswith("'") and json_str.endswith("'"):
                        json_str = json_str[1:-1]
                    elif json_str.startswith('"') and json_str.endswith('"'):
                        json_str = json_str[1:-1]
                    user_json = json.loads(json_str)
                    logger.debug(f"Parsed request: {json.dumps(user_json, indent=2)[:200]}")
                    return user_json
                except json.JSONDecodeError as e:
                    logger.debug(f"Failed to parse request JSON: {e}, raw: {msg_split[2][:100]}")
                    continue


def battle_is_finished(battle_tag, msg):
    return (
        msg.startswith(f">{battle_tag}")
        and ("|win|" in msg or "|tie|" in msg)
        and "|c|" not in msg
    )


async def simple_battle(ps_websocket_client: PSWebsocketClient, pokemon_battle_type: str):
    # Clean up old replay links at the start of a new battle
    await cleanup_old_replay_links()
    
    # Get battle tag and opponent
    battle_tag, opponent_name = await get_battle_tag_and_opponent(ps_websocket_client)
    battle_url = f"https://play.pokemonshowdown.com/{battle_tag}"
    logger.info(f"Battle started: {battle_tag} vs {opponent_name}")
    logger.info(f"Watch the battle live at: {battle_url}")
    
    await save_replay_link(battle_tag)
    saved_replay_link = battle_tag
    await ps_websocket_client.send_message(battle_tag, ["/savereplay"])
    logger.info("Saved replay link at battle start and created replay JSON")
    
    # Wait for player identifiers
    user_name = None
    while True:
        msg = await ps_websocket_client.receive_message()
        if "|player|" in msg:
            split_msg = msg.split("|")
            if len(split_msg) > 2:
                player_id = split_msg[2]
                player_name = split_msg[3] if len(split_msg) > 3 else ""
                if opponent_name in player_name:
                    user_name = "p2" if player_id == "p1" else "p1"
                    break
    
    logger.info(f"User is {user_name}")
    
    # Handle team preview if it exists
    team_preview_done = False
    
    while not team_preview_done:
        msg = await ps_websocket_client.receive_message()
        logger.debug(f"Received message (team preview check): {msg[:200]}")
        
        # Check if this message contains a team preview request
        request_json = None
        lines = msg.split("\n")
        for line in lines:
            if "|request|" in line:
                msg_split = line.split("|")
                if len(msg_split) > 2 and msg_split[1].strip() == "request" and msg_split[2].strip():
                    try:
                        json_str = msg_split[2].strip()
                        # Remove surrounding quotes if present
                        if json_str.startswith("'") and json_str.endswith("'"):
                            json_str = json_str[1:-1]
                        elif json_str.startswith('"') and json_str.endswith('"'):
                            json_str = json_str[1:-1]
                        request_json = json.loads(json_str)
                        # Check if it's a team preview request
                        if request_json.get("teamPreview"):
                            logger.info("Team preview request found in message")
                            break
                    except json.JSONDecodeError as e:
                        logger.debug(f"Failed to parse request JSON: {e}")
                        continue
        
        # If we found a team preview request, process it
        if request_json and request_json.get("teamPreview"):
            rqid = request_json.get("rqid", "")
            logger.info(f"Team preview request received, rqid: {rqid}")
            
            await ps_websocket_client.send_message(battle_tag, ["/savereplay"])
            await asyncio.sleep(1.5)
            logger.debug("Saved replay before team preview decision")
            
            ai_command = await get_ai_command(saved_replay_link, request_json, player_id=user_name)
            
            command = ai_command.strip()
            if rqid and f"|{rqid}" not in command:
                command += f"|{rqid}"
            logger.info(f"Sending team preview command: {command}")
            await ps_websocket_client.send_message(battle_tag, [command])
            team_preview_done = True
        
        # If we see |teampreview| but haven't gotten the request yet, wait for it
        elif "|teampreview|" in msg:
            logger.info("Team preview detected, waiting for request...")
            request_json = await wait_for_request(ps_websocket_client)
            if request_json and request_json.get("teamPreview"):
                rqid = request_json.get("rqid", "")
                logger.info(f"Team preview request received, rqid: {rqid}")
                
                await ps_websocket_client.send_message(battle_tag, ["/savereplay"])
                await asyncio.sleep(1.5)
                logger.debug("Saved replay before team preview decision")
                
                ai_command = await get_ai_command(saved_replay_link, request_json)
                
                command = ai_command.strip()
                if rqid and f"|{rqid}" not in command:
                    command += f"|{rqid}"
                logger.info(f"Sending team preview command: {command}")
                await ps_websocket_client.send_message(battle_tag, [command])
                team_preview_done = True
        
        if "|start|" in msg:
            logger.info("Battle started (no team preview)")
            team_preview_done = True
    
    # Wait for first request after battle starts
    request_json = await wait_for_request(ps_websocket_client)
    logger.debug(f"First request after battle start: {json.dumps(request_json, indent=2)[:500]}")
    
    # Send initial messages
    await ps_websocket_client.send_message(battle_tag, ["hf"])
    
    # Process the first request if it requires an action
    if request_json:
        rqid = request_json.get("rqid", "")
        logger.debug(f"First request after battle start: active={request_json.get('active')}, forceSwitch={request_json.get('forceSwitch')}, rqid={rqid}")
        
        await ps_websocket_client.send_message(battle_tag, ["/savereplay"])
        await asyncio.sleep(1.5)
        logger.debug("Sent /savereplay before first action")
        
        ai_command = await get_ai_command(saved_replay_link, request_json, player_id=user_name)
        
        command = ai_command.strip()
        # Add rqid if not already present
        if rqid and f"|{rqid}" not in command:
            command += f"|{rqid}"
        await ps_websocket_client.send_message(battle_tag, [command])
        logger.info(f"First action: executed command: {command}")
    
    logger.info("Battle started, waiting for actions...")
    
    # Battle loop
    turn_messages = []
    current_turn = 0
    replay_saved_for_turn = -1  # Track which turn we've saved replay for
    last_action_confirmed = True  # Track if our last action was confirmed (starts True for first action)
    
    while True:
        try:
            msg = await ps_websocket_client.receive_message()
        except ConnectionError as e:
            logger.error(f"Connection lost during battle: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error receiving message: {e}", exc_info=True)
            raise
        
        turn_messages.append(msg)
        
        # Check for action confirmation messages (our previous action was processed)
        if ("|move|" in msg or "|-terastallize|" in msg or "|switch|" in msg or 
            "|-end|" in msg or "|turn|" in msg):
            last_action_confirmed = True
            logger.debug("Action confirmed via battle message")
        
        # Detect new turn - this means the previous turn is complete
        if "|turn|" in msg:
            # Extract turn number
            try:
                turn_parts = msg.split("|turn|")
                if len(turn_parts) > 1:
                    new_turn = int(turn_parts[1].strip())
                    
                    # Always save replay when a new turn starts to keep state fresh
                    # This ensures the replay is updated with the latest battle state
                    await ps_websocket_client.send_message(battle_tag, ["/savereplay"])
                    await asyncio.sleep(1.0)  # Give server time to update replay
                    replay_saved_for_turn = new_turn
                    logger.debug(f"Sent /savereplay at start of turn {new_turn}")
                    
                    current_turn = new_turn
                    turn_messages = [msg]  # Start new turn with this message
                    logger.info(f"Turn {current_turn} started")
            except (ValueError, IndexError):
                pass
        
        # Check if battle is finished
        if battle_is_finished(battle_tag, msg):
            if "|win|" in msg:
                winner = msg.split("|win|")[-1].split("\n")[0].strip()
            else:
                winner = None
            logger.info(f"Battle finished. Winner: {winner}")
            
            # Final /savereplay command (replay link already saved at battle start)
            if current_turn >= 0:
                await ps_websocket_client.send_message(battle_tag, ["/savereplay"])
                logger.debug("Sent final /savereplay command")
            
            await ps_websocket_client.send_message(battle_tag, ["gg"])
            await ps_websocket_client.leave_battle(battle_tag)
            return winner
        
        # Check if we need to make a move or switch (request message)
        # Request can come in format: >battle-tag\n|request|{json}
        if "|request|" in msg:
            try:
                # Parse request from message
                lines = msg.split("\n")
                request_json = None
                for line in lines:
                    msg_split = line.split("|")
                    if len(msg_split) > 2 and msg_split[1].strip() == "request" and msg_split[2].strip():
                        json_str = msg_split[2].strip()
                        # Remove surrounding quotes if present
                        if json_str.startswith("'") and json_str.endswith("'"):
                            json_str = json_str[1:-1]
                        elif json_str.startswith('"') and json_str.endswith('"'):
                            json_str = json_str[1:-1]
                        request_json = json.loads(json_str)
                        break
                
                if not request_json:
                    continue
                    
                rqid = request_json.get("rqid", "")
                logger.debug(f"Request received: active={request_json.get('active')}, forceSwitch={request_json.get('forceSwitch')}, rqid={rqid}")
                
                # Wait for our previous action to be confirmed before saving replay
                # This ensures the replay includes our last action (e.g., terastallization)
                if not last_action_confirmed:
                    logger.debug("Waiting for previous action confirmation before saving replay...")
                    confirmation_timeout = 5.0  # seconds
                    start_time = asyncio.get_event_loop().time()
                    
                    # Wait for confirmation messages
                    while (asyncio.get_event_loop().time() - start_time) < confirmation_timeout:
                        try:
                            msg_check = await asyncio.wait_for(
                                ps_websocket_client.receive_message(), 
                                timeout=0.5
                            )
                            turn_messages.append(msg_check)
                            if ("|move|" in msg_check or "|-terastallize|" in msg_check or 
                                "|switch|" in msg_check or "|-end|" in msg_check or
                                "|turn|" in msg_check):
                                last_action_confirmed = True
                                logger.debug("Action confirmed via new message")
                                break
                        except asyncio.TimeoutError:
                            # No message received, continue waiting
                            continue
                
                await ps_websocket_client.send_message(battle_tag, ["/savereplay"])
                await asyncio.sleep(1.5)
                logger.debug(f"Saved replay before decision for turn {current_turn}")
                
                ai_command = await get_ai_command(saved_replay_link, request_json, player_id=user_name)
                
                command = ai_command.strip()
                # Add rqid if not already present
                if rqid and f"|{rqid}" not in command:
                    command += f"|{rqid}"
                await ps_websocket_client.send_message(battle_tag, [command])
                logger.info(f"Executed command: {command}")
                
                # Mark that we need to wait for confirmation of this action
                last_action_confirmed = False
            except (json.JSONDecodeError, IndexError, KeyError) as e:
                logger.warning(f"Error parsing request: {e}, message: {msg[:200]}")
                pass

