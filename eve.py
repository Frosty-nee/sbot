import datetime
from math import sqrt
import operator
import time

import sqlite3
import requests

import config

rs = requests.Session()
rs.headers.update({'User-Agent': 'sbot'})
if config.bot.eve_dsn is not None:
	conn = sqlite3.connect(config.bot.eve_dsn)
	curs = conn.cursor()

esi_price_cache = {'last_update': 0, 'items': {}}
def price_check(cmd):
	def __item_info(curs, query):
		curs.execute('''
			SELECT "typeID", "typeName" FROM "invTypes"
			WHERE LOWER("typeName") LIKE ? AND "marketGroupID" IS NOT NULL
			''', (query.lower(),))
		results = curs.fetchmany(3)
		if len(results) == 1:
			return results[0]
		if len(results) == 2 and \
				results[0][1].endswith('Blueprint') ^ results[1][1].endswith('Blueprint'):
			# an item and its blueprint; show the item
			if results[0][1].endswith('Blueprint'):
				return results[1]
			else:
				return results[0]
		if len(results) >= 2:
			return results
		return
	def item_info(item_name):
			# exact match
			curs.execute(
					'SELECT "typeID", "typeName" FROM "invTypes" WHERE LOWER("typeName") LIKE ?',
					(item_name.lower(),))
			result = curs.fetchone()
			if result:
				return result

			# start of string match
			results = __item_info(curs, item_name + '%')
			if isinstance(results, tuple):
				return results
			if results:
				names = map(lambda r: r[1], results)
				cmd.reply('Found items: ' + ', '.join(names))
				return None

			# substring match
			results = __item_info(curs, '%' + item_name + '%')
			if isinstance(results, tuple):
				return results
			if results:
				names = map(lambda r: r[1], results)
				cmd.reply('Found items: ' + ', '.join(names))
				return None
			cmd.reply('Item not found')
			return None
	def format_prices(prices):
		if prices is None:
			return 'n/a'
		if prices[1] < 1000.0:
			return 'bid {0:g} ask {1:g} vol {2:,d}'.format(*prices)
		prices = map(int, prices)
		return 'bid {0:,d} ask {1:,d} vol {2:,d}'.format(*prices)

	def get_esi_price(typeid):
		now = time.time()
		if esi_price_cache['last_update'] < now - 60 * 60 * 2:
			res = rs.get('https://esi.evetech.net/latest/markets/prices/?datasource=tranquility')
			if res.status_code == 200:
				esi_price_cache['items'].clear()
				for item in res.json():
					esi_price_cache['items'][item['type_id']] = item
		prices = esi_price_cache['items'][typeid]
		if prices and 'average_price' in prices:
			if prices['average_price'] < 1000.0:
				return 'avg {average_price:g} adj {adjusted_price:g}'.format(**prices)
			for k, v in prices.items():
				prices[k] = int(v)
			return 'avg {average_price:,d} adj {adjusted_price:,d}'.format(**prices)
		else:
			return 'n/a'

	if not cmd.args:
		return
	result = item_info(cmd.args)
	if not result:
		return
	typeid, item_name = result
	esi = get_esi_price(typeid)
	cmd.reply('%s: %s' % (item_name, esi))

def jumps(cmd):
	split = cmd.args.split()
	if len(split) > 3:
		cmd.reply('usage: `!jumps [from] [to]`')
		return
	results = []
	for i in range(2):
		curs.execute('''
				SELECT "solarSystemID" FROM "mapSolarSystems"
				WHERE "solarSystemName" LIKE ?
				''', (split[i],))
		results.append(operator.itemgetter(0)(curs.fetchone()))
	curs.fetchall()
	if len(results) < 2:
			cmd.reply('one or more systems not found')
			return
	if len(split) < 3:
		split.append('shortest')
	if len(split) == 3:
			secure_synonyms = ['safe', 'secure']
			if split[2] in secure_synonyms:
					split[2] = 'secure'
			else:
					split[2] = 'shortest'
	
	r = rs.get('https://esi.evetech.net/latest/route/{}/{}/?datasource=tranquility{}'
					.format(results[0], results[1],'&flag={}'.format(split[2])))
	try:
			data = r.json()
	except ValueError:
		cmd.reply('error getting jumps')
		return
	jumps_split = []
	for j in data:
			curs.execute('''SELECT "solarSystemName", "security" 
							FROM "mapSolarSystems" 
							WHERE "solarSystemID" = ?''', (j,))
			result = list(map(str,curs.fetchone()))
			result[1] = result[1][:3]
			jumps_split.append(" ".join(result))
	cmd.reply('{} jumps:\n'.format(len(jumps_split)-1) + " -> ".join(jumps_split))

def lightyears(cmd):
	split = [n + '%' for n in cmd.args.lower().split()]
	if len(split) != 2:
		cmd.reply('usage: !ly [from] [to]')
		return

	curs.execute('''
			SELECT "solarSystemName", x, y, z FROM "mapSolarSystems"
			WHERE LOWER("solarSystemName") LIKE ? OR LOWER("solarSystemName") LIKE ?
			''', (split[0], split[1]))
	result = curs.fetchmany(6)
	if len(result) < 2:
		cmd.reply('error: one or both systems not found')
		return
	elif len(result) > 2:
		cmd.reply('error: found too many systems: ' + ' '.join(map(operator.itemgetter(0), result)))
		return

	dist = 0
	for d1, d2 in zip(result[0][1:], result[1][1:]):
		dist += (d1 - d2)**2
	dist = sqrt(dist) / 9.4605284e15 # meters to lightyears
	ship_ranges = [
		('other:\t ', 3.5),
		('blops:\t ', 4.0),
		('JF:\t\t', 5.0),
		('super:\t ', 3.0),
	]
	jdc = []
	for ship, jump_range in ship_ranges:
		for level in range(0, 6):
			if dist <= jump_range * (1 + level * 0.2):
				jdc.append('%s%d' % (ship, level))
				break
		else:
			jdc.append(ship + 'N/A')
	cmd.reply('```%s ⟷ %s: %.3f ly\n%s```' % (result[0][0], result[1][0], dist, '\n'.join(jdc)))

def who(cmd):
	dt_format = '%Y-%m-%dT%H:%M:%SZ'
	char_id, corp_id, alliance_id, = None, None, None
	try:
		r = rs.post('https://esi.evetech.net/latest/universe/ids/',
			params={'datasource': 'tranquility', 'language': 'en-us'},
			json=[cmd.args])
		r.raise_for_status()
	
		if len(r.json().keys()) == 0:
			cmd.reply("%s: couldn't find your sleazebag" % cmd.sender['username'])
			return
		if 'characters' in r.json().keys():
			char_id = int(r.json()['characters'][0]['id'])
		elif 'corporations' in r.json().keys():
			corp_id = int(r.json()['corporations'][0]['id'])
		else:
			alliance_id = int(r.json()['alliances'][0]['id'])
		
		route='https://esi.evetech.net/latest/{}/{}'
		output=''
		if char_id:
				r=rs.get(route.format('characters', char_id))
				r.raise_for_status()
				data = r.json()
				char_name = data['name']
				corp_id = int(data['corporation_id'])
				birthday = data['birthday']
				birthday = datetime.datetime.strptime(birthday, dt_format).date()
				security_status = data['security_status']
				output = '%s: born %s, security status %.2f  ' % (char_name, birthday, security_status)
				output += 'https://zkillboard.com/character/%d/' % char_id
		if corp_id:
				r=rs.get(route.format('corporations', corp_id))
				data = r.json()
				print(data)
				corp_name = data['name']
				creation_date = data.get('creation_date') # NPC corps have no creation_date
				if creation_date:
					creation_date = str(datetime.datetime.strptime(creation_date, dt_format).date())
				else:
					creation_date = '?'
				members = data['member_count']
				alliance_id = data.get('alliance_id')
				output += '\n%s: created %s, %s members  ' % (corp_name, creation_date, members)
				output += 'https://zkillboard.com/corporation/%d/' % (corp_id)

		if alliance_id:
				r=rs.get(route.format('alliances', alliance_id))
				alliance_id = int(alliance_id)
				r = rs.get('https://esi.evetech.net/v2/alliances/%d/' % alliance_id)
				r.raise_for_status()
				data = r.json()
				alliance_name = data['alliance_name']
				founding_date = data['date_founded']
				founding_date = datetime.datetime.strptime(founding_date, dt_format).date()
				output += '\n%s: founded %s' % (alliance_name, founding_date)

		cmd.reply(output)
	except requests.exceptions.HTTPError:
		cmd.reply("%s: couldn't find your sleazebag" % cmd.sender['username'])
