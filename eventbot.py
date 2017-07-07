#!/usr/bin/python3
import asyncio, discord
import dataset, json
import sys, os
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from enum import Enum

# Obtain config from config.json
if os.path.isfile('config.json'):
    with open('config.json') as config_file:
        config = json.loads(config_file.read())
else:
    print('Could not find config.json!', file=sys.stderr)
    sys.exit()


# command messages
creator_id = '175753699224715264' # my discord user id
github_link = 'https://github.com/desolt/EventBot'
info_embed = discord.Embed(title='EventBot', 
                           type='rich', 
                           description='Helps discord admins manage events!\n\n' \
                                       'Created by <@{}>'.format(creator_id),
                           url=github_link)
info_embed.set_thumbnail(url='http://www.thefamouspeople.com/profiles/images/huey-long-2.jpg')

commands_message = '```css\n' \
                   'eb!info - shows this menu.\n' \
                   'eb!eventchannel <channel>\n' \
                   'eb!event <name> <mm/dd/yy> <hh:mm> - schedules an event\n' \
                   'eb!repeat <id> - toggles whether an event should repeat each week.\n' \
                   'eb!events [page #] - shows the current scheduled events\n' \
                   'eb!cancel <id> - cancels an event\n' \
                   'eb!subscribe <id> - subscribes to an event\n' \
                   'eb!unsubscribe <id> - unsubscribes from an event\n' \
                   'eb!subscriptions [page #] - lists subscribed events (DM only)\n' \
                   '```'

# Bot stuff
bot = discord.Client()

db = dataset.connect('mysql://{}:{}@{}/eventbot'
        .format(config['sql']['user'], config['sql']['pass'], config['sql']['host']))
event_table = db['events']
subscription_table = db['subscriptions']

# Meat and potatoes
async def check_schedule():
    while True:
        for event in event_table.all():
            if datetime.utcnow() < event['startsat']:
                continue

            channel = db['server_settings'].find_one(event['serverid'])
            if channel is not None: channel = channel['event_channel']
            if channel is None:
                channel = bot.get_server(event['serverid']).default_channel

            await bot.send_message(channel, 'Event "{}" (#{}) has started!'.format(event['name'], event['id']))
            for userid in subscription_table.find(eventid = event['id']):
                try:
                    user = await bot.get_user_info(userid['userid'])
                    print('Found user!')
                    await bot.send_message(user, 'Event "{}" has started!'.format(event['name']))
                except discord.NotFound: pass
            if 'repeat' not in event or not event['repeat']:
                subscription_table.delete(eventid = event['id'])

            if 'repeat' in event and event['repeat']: 
                # Delays the event to next week.
                newstartsat = event['startsat'] + datetime.timedelta(days = 7),
                event_table.update(dict(startsat = newstartsat, id = event['id']), ['startsat'])
            else:
                event_table.delete(id = event['id'])
            print('Event #{} has started!'.format(event['id']))

        await asyncio.sleep(60) # Wait every minute to check for an event.

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    if message.content.startswith('eb!'):
        await process_command(message.content[3:].split(' '), message)

class ErrorMessages(Enum):
    def __str__(self):
        return str(self.value)

    INVALID_ARG  = 'Invalid arguments!'
    PERMISSION   = 'You do not have permission to use this command'
    BAD_EVENT    = 'That event does not exist!'
    BAD_ID       = 'Invalid ID!'
    BAD_PAGE_NUM = 'Invalid page number!'

async def process_command(args, message):
    if args[0] in 'info':
        await bot.send_message(message.channel, embed=info_embed)
        await bot.send_message(message.author,  'Commands:\n{}'.format(commands_message))
        if not message.channel.is_private: # No point in saying commands have been DMed in the DMs.
            await bot.send_message(message.channel, 'The commands have been DMed to you!')
    elif args[0] in 'subscribe':
        if len(args) != 2:
            await bot.send_message(message.channel, ErrorMessages.INVALID_ARG)
            return

        try:
            id = int(args[1])
        except ValueError:
            await bot.send_message(message.channel, ErrorMessages.BAD_ID)

        event = event_table.find_one(id = id)
        if event is None:
            await bot.send_message(message.channel, ErrorMessages.BAD_EVENT)
        else:
            subscription_exists = subscription_table.find_one(userid = message.author.id, 
                eventid = event['id'])
            if subscription_exists is None:
                subscription_table.insert(dict(userid = message.author.id, 
                    eventid = event['id']))
                await bot.send_message(message.channel, 'You are now subscribed to event {}!'.format(event['id']))
            else:
                await bot.send_message(message.channel, 'You are already subscribed to that event!')

    # These commands cannot be executed through DMs.
    elif not message.channel.is_private:
        if args[0] in 'event':
            # Only admins can make events. TODO: Allow custom roles to make events w/ server settings.
            if not message.channel.permissions_for(message.author).administrator: 
                await bot.send_message(message.channel, ErrorMessages.PERMISSION)
                return

            if len(args) != 4:
                await bot.send_message(message.channel, str(ErrorMessages.INVALID_ARG))
                return

            dtstr = '{} {}'.format(args[2], args[3])
            try:
                dtobj = datetime.strptime(dtstr, '%m/%d/%y %H:%M')
                if datetime.utcnow() > dtobj:
                    await bot.send_message(message.channel, 'An event should take place in the future! (Remember to use UTC)')
                    return
            except ValueError:
                await bot.send_message(message.channel, 'Invalid datetime format!')
                return
            
            id = event_table.insert(dict(name = args[1], serverid = message.server.id, startsat = dtobj, repeat = False))

            embed = discord.Embed(title = 'Created a new event!', 
                                  description = args[1], 
                                  color = 0x5cc0f2, # Color is a nice sky blue.
                                  type = 'rich')
            embed.add_field(name = 'ID', value = str(id))
            embed.add_field(name = 'When', value = dtobj.strftime('%m/%d/%y %I:%M%p'))
            await bot.send_message(message.channel, embed = embed)
        elif args[0] in 'events' :
            if len(args) > 1:
                await bot.send_message(message.channel, ErrorMessages.INVALID_ARG)
                print(ErrorMessages.INVALID_ARG)
                print(str(ErrorMessages.INVALID_ARG))
                return

            output = '```\n'
            events = event_table.find(serverid = message.server.id)
            for event in events:
                dtstr = event['startsat'].strftime('%m/%d/%y %I:%M%p')
                output += 'Event #{} ({}) starts at {}\n'.format(event['id'], event['name'], dtstr)
            output += '```'

            await bot.send_message(message.channel, output)
    else: # DM only commands (namely eb!subscriptions)
        if args[0] in 'subscriptions':
            if len(args) > 1 and len(args) < 2:
                await bot.send_message(message.channel, ErrorMessages.INVALID_ARG)
                return

            page = 1 # default page
            if len(args) == 2:
                try:
                    page = int(args[1])
                    if page < 0: raise ValueError()
                except ValueError:
                    await bot.send_message(message.channel, ErrorMessages.BAD_PAGE_NUM)
                    return

            subscriptions = []
            for subscription in subscription_table.find(userid = message.author.id, 
                                                       order_by = ['id'], 
                                                       _limit=5, 
                                                       _offset=((page - 1) * 5)):
                subscriptions.append(event_table.find_one(id = subscription['eventid']))

            if len(subscriptions) == 0:
                await bot.send_message(message.channel, 'No subscriptions on page #{}.'.format(page))
                return

            embed = discord.Embed(title = 'Page #{} subscriptions'.format(page), color = 0xdafc1b)
            id = name = server = when = ''
            for event in subscriptions:
                if event is None: continue
                id += '{}\n'.format(event['id'])
                name += '{}\n'.format(event['name'])
                server += '{}\n'.format(bot.get_server(event['serverid']).name)
                when += '{}\n'.format(event['startsat'].strftime('%m/%d/%y %I:%M%p UTC'))
            embed.add_field(name = 'ID', value = id)
            embed.add_field(name = 'Name', value = name)
            embed.add_field(name = 'Server', value = server)
            embed.add_field(name = 'When', value = when)   
            await bot.send_message(message.author, embed = embed)

@bot.event
async def on_ready():
    await bot.change_presence(game = discord.Game(name='eb!info | https://github.com/desolt/EventBot'))
    print('EventBot is now online!')
    await check_schedule()

if __name__ == '__main__':
    bot.run(config['token'])
