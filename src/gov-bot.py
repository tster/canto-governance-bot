#!/usr/bin/python3

'''
Reece Williams (Reecepbcups | PBCUPS Validator) | February 9th, 2022
- Twitter bot to monitor and report on COSMOS governance proposals
- (Mar 8) Discord webhook to post proposals 
- (Mar 12) Discord Threads to allow for discussion of new proposals 

python3 -m pip install requests tweepy schedule discord

*Get REST lcd's in chain.json from https://github.com/cosmos/chain-registry
'''

import json
import os
import requests
import schedule
import time
import tweepy

# from _ChainApis import chainAPIs, customExplorerLinks, DAOs
from pyibc_api import get_chain, CHAIN_APIS, CUSTOM_EXPLORER_LINKS, PAGES, DAOs, REST_ENDPOINTS # get_dao?

# Don't touch below --------------------------------------------------
proposals = {}
DISCORD_API = "https://discord.com/api/v9"
IS_FIRST_RUN = False
BOOSTED_DISCORD_THREAD_TIME_TIERS = {0: 1440,1: 4320,2: 10080,3: 10080}

if not os.path.isfile("secrets.json"):
    print("\nsecrets.json not found, please create it like so:")
    print("cp secrets.example.json secrets.json\n")
    exit()

PREFIX="COSMOSGOV"
with open('secrets.json', 'r') as f:
    secrets = json.load(f)

    IN_PRODUCTION = secrets['IN_PRODUCTION']
    TWITTER = secrets['TWITTER']['ENABLED']
    DISCORD = secrets['DISCORD']['ENABLED']
    DISCORD_THREADS_AND_REACTIONS = secrets['DISCORD_THREADS']['ENABLE_THREADS_AND_REACTIONS']
    explorer = secrets['EXPLORER_DEFAULT'] # ping, mintscan, keplr
    USE_CUSTOM_LINKS = secrets['USE_CUSTOM_LINKS']

    # If false, it is up to you to schedule via crontab -e such as: */30 * * * * cd /root/twitterGovBot && python3 twitterGovernanceBot.py
    USE_PYTHON_RUNNABLE = secrets['USE_PYTHON_RUNNABLE']
    SCHEDULE_SECONDS = 60 * int(secrets['MINUTES_BETWEEN_RUNNABLE'])

    LOG_RUNS = secrets['LOG_RUNS']    

    TICKERS_TO_ANNOUNCE = secrets.get('TICKERS_TO_ANNOUNCE', [])
    TICKERS_TO_IGNORE = secrets.get('TICKERS_TO_IGNORE', [])
    # print(f"Ignoring: {TICKERS_TO_IGNORE}")

    filename = secrets['FILENAME']
    filename_dao = 'chains_dao.json'
    
    # print(f"\nOS ENV: {os.environ}")
    if TWITTER:
        print("Pushing to Twitter")
        twitSecrets = secrets['TWITTER']
        APIKEY = os.getenv(f"{PREFIX}_TWITTER_APIKEY", twitSecrets['APIKEY'])
        APIKEYSECRET = os.getenv(f"{PREFIX}_TWITTER_APIKEYSECRET", twitSecrets['APIKEYSECRET'])
        ACCESS_TOKEN = os.getenv(f"{PREFIX}_TWITTER_ACCESS_TOKENT", twitSecrets['ACCESS_TOKEN'])
        ACCESS_TOKEN_SECRET = os.getenv(f"{PREFIX}_TWITTER_ACCESS_TOKEN_SECRET", twitSecrets['ACCESS_TOKEN_SECRET'])
        # Authenticate to Twitter
        api = tweepy.Client(consumer_key=APIKEY, consumer_secret=APIKEYSECRET, access_token=ACCESS_TOKEN, access_token_secret=ACCESS_TOKEN_SECRET, wait_on_rate_limit=True)

    if DISCORD:
        print("Pushing to Discord")
        discSecrets = secrets['DISCORD']
        # print(discSecrets)
        WEBHOOK_URL = os.getenv(f"{PREFIX}_DISCORD_WEBHOOK_URL", discSecrets['WEBHOOK_URL'])        
        AVATAR_URL = os.getenv(f"{PREFIX}_DISCORD_AVATAR_URL", discSecrets['AVATAR_URL'])
        HEX_COLOR = int(os.getenv(f"{PREFIX}_DISCORD_HEX_COLOR", discSecrets['HEX_COLOR']), 16)        
        REACTION_RATE_LIMIT = 0.1

        if DISCORD_THREADS_AND_REACTIONS:
            discTreads = secrets['DISCORD_THREADS']
            CHANNEL_ID = int(discTreads['CHANNEL_ID'])
            GUILD_ID = int(discTreads['GUILD_SERVER_ID'])
            DO_ARCHIVE_THREADS = bool(discTreads['ARCHIVE_THREADS'])
            THREAD_ARCHIVE_MINUTES = int(discTreads['THREAD_ARCHIVE_MINUTES'])
            BOT_TOKEN = discTreads['BOT_TOKEN']                 
            BOT_TOKEN_HEADERS_FOR_API = {
                "Content-Type": "application/json",
                "authorization": "Bot " + BOT_TOKEN,    
            }

# Loads normal proposals (ticker -> id) dict
def load_proposals_from_file() -> dict:
    global proposals
    with open(filename, 'r') as f:
        proposals = json.load(f)       
    return proposals
def save_proposals() -> None:
    if len(proposals) > 0:
        with open(filename, 'w') as f:
            json.dump(proposals, f)
def update_proposal_value(ticker: str, newPropNumber: int):
    global proposals
    proposals[ticker] = newPropNumber
    save_proposals()
#

def _SetMaxArchiveDurationLength() -> int:
    global THREAD_ARCHIVE_MINUTES

    if DISCORD_THREADS_AND_REACTIONS == False:
        return 0

    # Archive lengths are 1 or 24 hours for level 0 boosted servers, 3 days for level 1, and 7 days for level 2
    # Returns max time user
    v = requests.get(f"{DISCORD_API}/guilds/{GUILD_ID}", headers=BOT_TOKEN_HEADERS_FOR_API).json()    
    # print(v)
    
    if 'message' in v.keys() and v['message'] == '401: Unauthorized':
        print("Discord API Error: 401 Unauthorized. Please ensure you have the correct BOT_TOKEN set in secrets.json")
        exit()

    guildBoostLevel = int(v['premium_tier'])
    max_len = BOOSTED_DISCORD_THREAD_TIME_TIERS[guildBoostLevel]
    
    if THREAD_ARCHIVE_MINUTES not in [60, 1440, 4320, 10080]:
        THREAD_ARCHIVE_MINUTES = max_len
        print(f"\nInvalid thread archive length: {THREAD_ARCHIVE_MINUTES}")
        print(f"Using {max_len} minutes. Other options: [60, 1440, 4320, 10080]")
    elif THREAD_ARCHIVE_MINUTES > max_len:
        THREAD_ARCHIVE_MINUTES = max_len
        print(f"\nWARNING: THREAD_ARCHIVE_MINUTES is greater than the max archive length for this server. Setting to {max_len}")
        print(f"You need a higher boost level to use 4320 & 100080 sadly :(")

    return max_len

def discord_create_thread(message_id, thread_name):
    global DO_ARCHIVE_THREADS
    data = { # https://discord.com/developers/docs/resources/channel#allowed-mentions-object-json-params-thread
        "name": thread_name,
        "archived": DO_ARCHIVE_THREADS,
        "auto_archive_duration": THREAD_ARCHIVE_MINUTES, # set via _SetMaxArchiveDurationLength on main() based on server boost level
        "locked": False,
        "invitable": False,
        "rate_limit_per_user": 5,
    }
    # print(data)
    # https://discord.com/developers/docs/topics/gateway#thread-create
    return requests.post(f"{DISCORD_API}/channels/{CHANNEL_ID}/messages/{message_id}/threads", json=data, headers=BOT_TOKEN_HEADERS_FOR_API).json()    

def _getLastMessageID():
    # gets last message from channel that the webhook just sent too. This way we can make thread from it without bot running all the time
    # https://discord.com/developers/docs/resources/channel#get-channel-messages
    res = requests.get(f"{DISCORD_API}/channels/{CHANNEL_ID}/messages?limit=1", headers=BOT_TOKEN_HEADERS_FOR_API).json()
    # print(res)
    return res[0]['id']

from utils.notifications import discord_notification

def discord_post_to_channel(ticker, propID, title, description, voteLink):
    # Auto replace description's <br> & \n ?
    if len(description) > 4096:
        description = description[:4090] + "....."

    discord_notification(
        url=WEBHOOK_URL,
        title=f"${str(ticker).upper()} #{propID} | {title}", 
        description=description,
        color=HEX_COLOR,
        values={"vote": ["https://canto.io/governance", False]},
        imageLink=AVATAR_URL
    )

def discord_add_reacts(message_id): # needs READ_MESSAGE_HISTORY & ADD_REACTIONS
    # https://discord.com/developers/docs/resources/channel#create-reaction
    # https://discord.com/developers/docs/resources/emoji    
    for emoji in ["✅", "❌", "⭕", "🚫"]:
        # print("PUT request for emoji: " + emoji) # DEBUGGING
        r = requests.put(f"{DISCORD_API}/channels/{CHANNEL_ID}/messages/{message_id}/reactions/{emoji}/@me", headers=BOT_TOKEN_HEADERS_FOR_API)
        if r.text != "":
            print(r.text)
        time.sleep(REACTION_RATE_LIMIT) # rate limit

def get_explorer_link(ticker, propId):
    if USE_CUSTOM_LINKS and ticker in CUSTOM_EXPLORER_LINKS:
        return f"{CUSTOM_EXPLORER_LINKS[ticker]}/{PAGES[ticker]['gov_page'].replace('{id}', str(propId))}"

    # pingpub, mintscan, keplr
    # possibleExplorers = chainAPIs[ticker][1]
    chain_info = get_chain(ticker)
    possibleExplorers = chain_info['explorers']

    explorerToUse = explorer
    if explorerToUse not in possibleExplorers: # If it doesn't have a mintscan, default to ping.pub (index 0)
        explorerToUse = list(possibleExplorers.keys())[0]

    url = f"{chain_info['explorers'][explorerToUse]}/{PAGES[explorerToUse]['gov_page'].replace('{id}', str(propId))}"
    # print('get_explorer_link', url)
    return url

# This is so messy, make this more OOP related
def post_update(ticker, propID, title, description="", isDAO=False, DAOVoteLink=""):
    chainExploreLink = DAOVoteLink
    if isDAO == False:
        chainExploreLink = get_explorer_link(ticker, propID)

    message = f"${str(ticker).upper()} | Proposal #{propID} | VOTING | {title} | https://canto.io/governance"
    print(message)

    if IN_PRODUCTION:
        try:
            if TWITTER:
                tweet = api.create_tweet(user_auth=True,text=message)
                print(f"Tweet sent")
            if DISCORD:
                discord_post_to_channel(ticker, propID, title, description, chainExploreLink)
                if DISCORD_THREADS_AND_REACTIONS:
                    # Threads must be enabled for reacts bc bot token
                    discord_add_reacts(_getLastMessageID())
                    discord_create_thread(_getLastMessageID(), f"{ticker}-{propID}") 
                    pass
        except Exception as err:
            print("Tweet failed due to being duplicate OR " + str(err)) 
    
    
def getAllProposals(ticker) -> list:
    # Makes request to API & gets JSON reply in form of a list
    props = []
    
    try:
        link = 'https://rest.cosmos.directory/canto/cosmos/gov/v1beta1/proposals?proposal_status=2'
        response = requests.get(link, headers={'accept': 'application/json'})
        props = response.json()['proposals']
        print(props)
    except Exception as e:
        print(f"Issue with request to {ticker}: {e}")
    return props

def checkIfNewerDAOProposalIsOut(daoTicker):
    print(f"Checking if new DAO proposal is out for {daoTicker}")
    # https://rest-juno.ecostake.com/cosmwasm/wasm/v1/contract/juno1eqfqxc2ff6ywf8t278ls3h3rdk7urmawyrthagl6dyac29r7c5vqtu0zlf/smart/eyJsaXN0X3Byb3Bvc2FscyI6e319?encoding=base64
    token = DAOs[daoTicker]
    props = requests.get(f"{token['proposals']}").json()['data']['proposals']

    for prop in props:
        current_prop_id = int(prop['id'])
        current_id_str = str(current_prop_id)
        print(f"{daoTicker} | {current_prop_id}")

        proposal_title = prop['proposal']['title']
        proposer = prop['proposal']['proposer']

        status = prop['proposal']['status']
        if status != "open": # executed, or maybe no deposit yet.
            print(f"Proposal {current_prop_id} is not open for voting yet, skipping")
            continue

        if daoTicker not in list(proposals.keys()):
            proposals[daoTicker] = 0 #; print('token not in dict, adding')

        # check if this proposal has been submitted before based on the # id
        if current_prop_id <= proposals[daoTicker]:
            print(f"Proposal {current_prop_id} was already posted for this id ({current_prop_id})")
            continue

        print(f"{daoTicker} has not been posted before as: {current_prop_id} | {proposal_title}")

        if IS_FIRST_RUN == False: # we only write DAO proposals to discord / twitter when its not the first run or it would spam ALL proposals on start
            print(f"Proposal {current_prop_id} exists")
            # Announce it as live
            # title = f"{token['name']} Proposal #{current_prop_id}"
            post_update(
                ticker=daoTicker,
                propID=current_prop_id, 
                title=proposal_title, 
                description=f"from {proposer}", # for discord embeds
                isDAO=True,
                DAOVoteLink=f"{token['vote']}/{current_prop_id}" # https://www.rawdao.zone/vote/#
            )

        if IS_FIRST_RUN or IN_PRODUCTION:      
            # save to proposals dict & to file (so we don't post again), unless its the first run                                 
            update_proposal_value(daoTicker, current_prop_id)
        else:
            print("DAO: Not in production, not writing to file.")


def checkIfNewestProposalIDIsGreaterThanLastTweet(ticker):
    # get our last tweeted proposal ID (that was in voting period), if it exists
    # if not, 0 is the value so we search through all proposals
    print("looking for newest prop id")
    lastPropID = 0
    if ticker in proposals:
        lastPropID = int(proposals[ticker])

    # gets JSON list of all proposals
    props = getAllProposals(ticker)

    if len(props) == 0:
        print("no proposal found")
        return

    # loop through out last stored voted prop ID & newest proposal ID
    for prop in props:
        current_prop_id = int(prop['proposal_id'])
        print(current_prop_id)

        # If this is a new proposal which is not the last one we tweeted for
        if current_prop_id > lastPropID:   
            print(f"Newest prop ID {current_prop_id} > last prop ID: {lastPropID}")
            
            if IS_FIRST_RUN or IN_PRODUCTION:      
                # save to proposals dict & to file (so we don't post again), unless its the first run                                 
                update_proposal_value(ticker, current_prop_id)
            else:
                print("Not in production, not writing to file.")

            post_update(
                ticker=ticker,
                propID=current_prop_id, 
                title=prop['content']['title'], 
                description=prop['content']['description'], # for discord embeds
            )

def logRun():
    if LOG_RUNS:
        with open("logs.txt", 'a') as flog:
            flog.write(str(time.ctime() + "\n"))

def runChecks():   
    print("Running checks...") 
    for chain in CHAIN_APIS.keys():
        try:
            if  len(TICKERS_TO_ANNOUNCE) > 0 and chain not in TICKERS_TO_ANNOUNCE:
                continue
            if len(TICKERS_TO_IGNORE) > 0 and chain in TICKERS_TO_IGNORE:
                # print(f"Ignoring {chain} as it is in the ignore list.")
                continue # ignore chains like terra we don't want to announce

            print("checkIfNewest")
            print(chain)
            checkIfNewestProposalIDIsGreaterThanLastTweet(chain)
        except Exception as e:
            print(f"{chain} checkProp failed: {e}")


    # loop through DAOs
    for dao in DAOs.keys():
        try:
            if dao not in TICKERS_TO_ANNOUNCE and TICKERS_TO_ANNOUNCE != []:
                continue

            print("checkIfNewer")
            print(chain)
            checkIfNewerDAOProposalIsOut(dao)
        except Exception as e:
            print(f"{dao} checkProp failed {e}")

    logRun()
    print(f"All chains checked {time.ctime()}, waiting")


def updateChainsToNewestProposalsIfThisIsTheFirstTimeRunning():
    global IN_PRODUCTION, IS_FIRST_RUN
    '''
    Updates JSON file to the newest proposals provided this is the first time running
    '''
    if os.path.exists(filename):
        print(f"{filename} exists, not first run")
        return

    IS_FIRST_RUN = True
    if IN_PRODUCTION:
        IN_PRODUCTION = False
        
    print("Updating chains to newest values since you have not run this before, these will not be posted")
    runChecks()
    save_proposals()
    print("Run this again now, chains have been populated")
    exit(0)

if __name__ == "__main__":        
    updateChainsToNewestProposalsIfThisIsTheFirstTimeRunning()

    load_proposals_from_file()    
    _SetMaxArchiveDurationLength()

    # informs user & setups of length of time between runs
    if IN_PRODUCTION:        
        print("[!] BOT IS RUNNING IN PRODUCTION MODE!!!!!!!!!!!!!!!!!!")
        time.sleep(5)

        output = "[!] Running "
        if TICKERS_TO_ANNOUNCE == []:
            output += "all in 2 seconds"
        else:
            output += f"{TICKERS_TO_ANNOUNCE} in 2 seconds"
        print(output)
        time.sleep(2)
    else:
        SCHEDULE_SECONDS = 3
        print("Bot is in test mode...")

    if DISCORD:
        print("DISCORD module enabled")
    if TWITTER:
        print("TWITTER module enabled")

    runChecks()

    # If user does not use a crontab, this can be run in a screen/daemon session
    if USE_PYTHON_RUNNABLE:      
        schedule.every(SCHEDULE_SECONDS).seconds.do(runChecks)  
        while True:
            print("Running runnable then waiting...")
            schedule.run_pending()
            time.sleep(SCHEDULE_SECONDS)
            

    
