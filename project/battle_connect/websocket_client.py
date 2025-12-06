import asyncio
import websockets
import requests
import json
import time
import logging

logger = logging.getLogger(__name__)


class LoginError(Exception):
    pass


class SaveReplayError(Exception):
    pass


class PSWebsocketClient:
    websocket = None
    address = None
    login_uri = None
    username = None
    password = None
    last_message = None
    last_challenge_time = 0

    @classmethod
    async def create(cls, username, password, address):
        self = PSWebsocketClient()
        self.username = username
        self.password = password
        self.address = address
        
        logger.info(f"Attempting to connect to {address}...")
        # Add timeout and connection settings
        try:
            logger.debug("Creating websocket connection...")
            self.websocket = await asyncio.wait_for(
                websockets.connect(
                    self.address,
                    ping_interval=None,
                    close_timeout=10
                ),
                timeout=30  # 30 second timeout for connection
            )
            logger.info("Websocket connection established successfully")
        except asyncio.TimeoutError:
            logger.error(f"Connection to {address} timed out after 30 seconds")
            raise ConnectionError(f"Connection to {address} timed out after 30 seconds")
        except Exception as e:
            logger.error(f"Failed to connect to {address}: {e}", exc_info=True)
            raise ConnectionError(f"Failed to connect to {address}: {e}")
        
        self.login_uri = "https://play.pokemonshowdown.com/api/login"
        return self

    async def join_room(self, room_name):
        message = "/join {}".format(room_name)
        await self.send_message("", [message])
        logger.debug("Joined room '{}'".format(room_name))

    async def receive_message(self):
        message = await self.websocket.recv()
        logger.debug("Received message from websocket: {}".format(message))
        return message

    async def send_message(self, room, message_list):
        message = room + "|" + "|".join(message_list)
        logger.debug("Sending message to websocket: {}".format(message))
        await self.websocket.send(message)
        self.last_message = message

    async def avatar(self, avatar):
        await self.send_message("", ["/avatar {}".format(avatar)])
        await self.send_message("", ["/cmd userdetails {}".format(self.username)])
        while True:
            # Wait for the query response and check the avatar
            # |queryresponse|QUERYTYPE|JSON
            msg = await self.receive_message()
            msg_split = msg.split("|")
            if msg_split[1] == "queryresponse":
                user_details = json.loads(msg_split[3])
                if user_details["avatar"] == avatar:
                    logger.info("Avatar set to {}".format(avatar))
                else:
                    logger.warning(
                        "Could not set avatar to {}, avatar is {}".format(
                            avatar, user_details["avatar"]
                        )
                    )
                break

    async def close(self):
        await self.websocket.close()

    async def get_id_and_challstr(self):
        logger.info("Waiting for challstr message...")
        max_attempts = 50
        attempt = 0
        while attempt < max_attempts:
            try:
                message = await asyncio.wait_for(self.receive_message(), timeout=10)
                logger.debug(f"Received message while waiting for challstr: {message[:200]}")
                split_message = message.split("|")
                if len(split_message) > 1 and split_message[1] == "challstr":
                    logger.info(f"Received challstr: client_id={split_message[2]}, challstr={split_message[3][:20]}...")
                    return split_message[2], split_message[3]
                attempt += 1
            except asyncio.TimeoutError:
                logger.warning(f"Timeout waiting for challstr (attempt {attempt + 1}/{max_attempts})")
                attempt += 1
                if attempt >= max_attempts:
                    raise ConnectionError("Timeout waiting for challstr message from server")
        
        raise ConnectionError("Failed to receive challstr message")

    async def login(self):
        logger.info(f"Logging in as {self.username} (unregistered: {self.password is None})...")
        client_id, challstr = await self.get_id_and_challstr()
        challstr_combined = "|".join([client_id, challstr])
        logger.debug(f"challstr_combined length: {len(challstr_combined)}")
        
        proxy = None
        
        logger.info(f"Making login request to {self.login_uri}...")
        if self.password:
            logger.debug("Using registered account login")
            response = requests.post(
                self.login_uri,
                data={
                    "name": self.username,
                    "pass": self.password,
                    "challstr": challstr_combined,
                },
                proxies=proxy,
                timeout=10
            )
            logger.debug(f"Login response status: {response.status_code}")
            logger.debug(f"Login response text (first 200 chars): {response.text[:200]}")

        else:
            logger.debug("Using unregistered guest login")
            response = requests.post(
                self.login_uri,
                data={
                    "act": "getassertion",
                    "userid": self.username,
                    "challstr": challstr_combined,
                },
                proxies=proxy,
                timeout=10
            )
            logger.debug(f"Login response status: {response.status_code}")
            logger.debug(f"Login response text (first 200 chars): {response.text[:200]}")

        if response.status_code == 200:
            if self.password:
                logger.debug("Parsing registered account response")
                response_json = json.loads(response.text[1:])
                if "actionsuccess" not in response_json:
                    logger.error("Login Unsuccessful: {}".format(response_json))
                    raise LoginError("Could not log-in: {}".format(response_json))

                assertion = response_json.get("assertion")
                logger.debug(f"Got assertion (length: {len(assertion) if assertion else 0})")
            else:
                assertion = response.text.strip()
                logger.debug(f"Got assertion (length: {len(assertion)})")
                if assertion == ";":
                    logger.error(f"Username '{self.username}' is registered. Use --password for registered accounts.")
                    raise LoginError(f"Username '{self.username}' is already registered")
                if assertion.startswith(";;"):
                    logger.error(f"Login error: {assertion}")
                    raise LoginError(f"Could not log-in: {assertion}")

            message = ["/trn " + self.username + ",0," + assertion]
            logger.info(f"Sending /trn command (message length: {len(message[0])})")
            await self.send_message("", message)
            logger.info("Waiting 3 seconds for login to process...")
            await asyncio.sleep(3)
            logger.info("Login complete!")
        else:
            logger.error(f"Could not log-in. Status code: {response.status_code}")
            logger.error(f"Response content: {response.content[:500]}")
            raise LoginError(f"Could not log-in: HTTP {response.status_code}")

    async def update_team(self, battle_format, team):
        if team:
            from battle_connect.team_converter import export_to_packed
            
            try:
                packed_team = export_to_packed(team.strip())
                logger.debug(f"Converted team to packed format (first 200 chars): {packed_team[:200]}")
                
                message = [f"/utm {packed_team}"]
                await self.send_message("", message)
                logger.info("Team updated for {}".format(battle_format))
            except Exception as e:
                logger.error(f"Failed to convert team to packed format: {e}")
                raise ValueError(f"Team conversion failed: {e}")
        else:
            logger.error("No team provided for {}. This format requires a team!".format(battle_format))
            raise ValueError(f"Team is required for {battle_format} but none was provided")

    async def challenge_user(self, user_to_challenge, battle_format, team):
        logger.info("Challenging {}...".format(user_to_challenge))
        await self.update_team(battle_format, team)
        message = ["/challenge {},{}".format(user_to_challenge, battle_format)]
        await self.send_message("", message)
        self.last_challenge_time = time.time()

    async def accept_challenge(self, battle_format, team, room_name):
        if room_name is not None:
            await self.join_room(room_name)

        logger.info("Waiting for a {} challenge".format(battle_format))
        await self.update_team(battle_format, team)
        username = None
        while username is None:
            msg = await self.receive_message()
            split_msg = msg.split("|")
            if (
                len(split_msg) == 9
                and split_msg[1] == "pm"
                and split_msg[3].strip().replace("!", "").replace("‽", "")
                == self.username
                and split_msg[4].startswith("/challenge")
                and split_msg[5] == battle_format
            ):
                username = split_msg[2].strip()

        message = ["/accept " + username]
        await self.send_message("", message)

    async def search_for_match(self, battle_format, team, room_names):
        if len(room_names) > 0:
            for room in room_names:
                await self.join_room(room)
        logger.info("Searching for ranked {} match".format(battle_format))
        await self.update_team(battle_format, team)
        message = ["/search {}".format(battle_format)]
        await self.send_message("", message)

    async def leave_battle(self, battle_tag):
        message = ["/leave"]
        await self.send_message(battle_tag, message)

        while True:
            msg = await self.receive_message()
            if battle_tag in msg and "deinit" in msg:
                return

    async def save_replay(self, battle_tag):
        message = ["/savereplay"]
        await self.send_message(battle_tag, message)
