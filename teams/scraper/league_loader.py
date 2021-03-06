from decimal import Decimal
import datetime
import re

from bs4 import BeautifulSoup

from teams.models import League, Player, ScoreEntry, Team, Scorecard, ScorecardEntry, PlayerScoreStats, DraftClaim, \
    TradeEntry, AddDrop
from teams.scraper.html_scrapes import get_leagues_from_entrance


__author__ = 'bprin'

import logging

logger = logging.getLogger(__name__)


def __get_player_id_from_playerpage(html):
    try:
        return re.findall(r'playerId=(\d+)', html)[0]
    except IndexError:
        f = open('error_html', 'w')
        f.write(html)
        raise


def __get_player_name_from_playerpage(html):
    pool = BeautifulSoup(html)
    return pool.find_all('div', 'player-name')[0].string


def __get_player_position_from_playerpage(html):
    pool = BeautifulSoup(html)
    return pool.find('span', {"title": "Position Eligibility"}).contents[1].strip()


def load_leagues_from_entrance(html, espn_user):
    logger.debug("loading leagues from entrance for user %s" % espn_user.username)
    league_tuples = get_leagues_from_entrance(html)
    leagues = []
    for league_tuple in league_tuples:
        try:
            league = League.objects.get(espn_id=league_tuple[1], year=league_tuple[2])
        except League.DoesNotExist:
            league = League.objects.create(espn_id=league_tuple[1], year=league_tuple[2], name=league_tuple[0],
                                           loaded=False, scraped_weeks=0)
        leagues.append(league)

        try:
            team = Team.objects.get(league=league, espn_id=league_tuple[3])
            logger.debug("team already existed")
        except Team.DoesNotExist:
            logger.debug("creating new team")
            team = Team.objects.create(league=league, espn_id=league_tuple[3])
        logger.debug("saving user %s to team %s" % (espn_user.username, team.team_name))
        team.espn_user = espn_user
        team.save()

    return leagues


def get_passing_stats(player_score_stats, stats):
    player_score_stats.pass_yards = int(stats[2])
    player_score_stats.pass_td = int(stats[3])
    (interceptions, fumbles) = stats[4].split('/') if stats[4] != 0 else ('0', '0')
    player_score_stats.interceptions = int(interceptions.strip())
    player_score_stats.fumbles = int(fumbles.strip())
    player_score_stats.default_points = Decimal(stats[5])
    player_score_stats.save()


def get_running_stats(player_score_stats, stats):
    player_score_stats.run_yards = int(stats[3])
    player_score_stats.run_td = int(stats[3])
    player_score_stats.default_points = Decimal(stats[5])
    player_score_stats.save()


def get_receiving_stats(player_score_stats, stats):
    player_score_stats.receptions = int(stats[2])
    player_score_stats.receiving_yards = int(stats[3])
    player_score_stats.receving_td = int(stats[4])
    player_score_stats.save()


def get_special_stats(player_score_stats, stats):
    player_score_stats.blocked_kr = int(stats[2])
    player_score_stats.int_td = int(stats[3])
    player_score_stats.fr_td = int(stats[4])
    player_score_stats.save()


def get_kicking_stats(player_score_stats, stats):
    player_score_stats.pat_made = Decimal(stats[4])
    fg_made = Decimal(stats[2])
    fg_attempted = Decimal(stats[3])
    fg_missed = fg_attempted - fg_made
    player_score_stats.fg_missed = fg_missed
    player_score_stats.save()


def get_fg_distance_stats(player_score_stats, stats):
    player_score_stats.fg_0 = Decimal(stats[2])
    player_score_stats.fg_40 = Decimal(stats[3])
    player_score_stats.fg_50 = Decimal(stats[4])
    player_score_stats.save()


def get_stats_function(headers):
    if headers == ['WK', 'OPP', 'YDS', 'TD', 'I/F', 'PTS']:
        return get_passing_stats
    elif headers == [u'WK', u'OPP', u'ATT', u'YDS', u'TD', u'PTS']:
        return get_running_stats
    elif headers == [u'WK', u'OPP', u'REC', u'YDS', u'TD', u'PTS']:
        return get_receiving_stats
    elif headers == [u'WK', u'OPP', u'BLKKRTD', u'INTTD', u'FRTD', u'PTS']:
        return get_special_stats
    elif headers == [u'WK', u'OPP', u'FGM', u'FGA', u'XPM', u'PTS']:
        return get_kicking_stats
    elif headers == [u'WK', u'OPP', u'1-39', u'40-49', u'50+', u'PTS']:
        return get_fg_distance_stats
    elif headers == [u'WK', u'OPP', u'PA', u'I', u'FR', u'TD', u'PTS']:
        pass
    elif headers == [u'WK', u'OPP', u'PTD', u'KTD', u'SCK', u'PTS']:
        pass
    else:
        raise Exception("unknown headers %s" % str(headers))


def load_scores_from_playersheet(html, player_id, year, overwrite=False):
    pool = BeautifulSoup(html)

    name = __get_player_name_from_playerpage(html)
    # player_id = __get_player_id_from_playerpage(html)
    position = __get_player_position_from_playerpage(html)
    new = True

    try:
        player = Player.objects.get(name=name, position=position)
    except Player.DoesNotExist:
        new = False

        try:
            player = Player.objects.get(name=name)
            player.position = position
            player.save()
        except Player.DoesNotExist:
            player = Player.objects.create(name=name, espn_id=player_id, position=position)
            logger.debug("creating player with position %s " % player.name)

    player.espn_id = player_id
    player.position = position
    player.save()

    if not new:
        entries = ScoreEntry.objects.filter(player=player, year=year)
        if len(entries) > 0:
            if not overwrite:
                return
            else:
                entries.delete()

    stats_tables = pool.find_all(id=re.compile('moreStatsView.*'))
    for stats_table in stats_tables:
        headers = [tr.string.strip() for tr in stats_table.find_all('tr')[0].find_all('td')]
        stats_function = get_stats_function(headers)
        for i, row in enumerate(stats_table.find_all('tr')[1:]):
            week = i + 1
            sc = ScoreEntry.objects.get_or_create(week=week, year=year, player=player)[0]
            stats = [td.string for td in row]
            stats = map(lambda stat: 0 if stat == '-' else stat, stats)
            pss = PlayerScoreStats.objects.get_or_create(score_entry=sc)[0]
            if stats_function:
                stats_function(pss, stats)
            if not pss.default_points:
                pss.default_points = Decimal(stats[5])
                pss.save()


def load_teams_from_standings(html, league):
    soup = BeautifulSoup(html)

    rows = soup.find_all('span', 'alt-info')
    rows = filter(lambda row: len(row.parent.contents) < 3, rows)
    rows = map(lambda row: row.parent, rows)
    extract_fields = lambda row: (row.span.string[1:-1],
                                  row.contents[0].strip(),
                                  re.search(r'teamId=(\d*)', row['href']).group(1)
    )
    rows = map(extract_fields, rows)
    for row in rows:
        abbreviation = row[0]
        team_name = row[1]
        espn_id = row[2]

        try:
            logger.debug("load_team_from_standings(): searching for espn_id %s" % espn_id)
            team = Team.objects.get(league=league, espn_id=espn_id)
            logger.debug("load_teams_from_standings(): team %s already existed, with espn_user=%s" % (
            team.team_name, team.espn_user))
        except Team.DoesNotExist:
            team = Team.objects.create(team_name=team_name, espn_id=espn_id, abbreviation=abbreviation, league=league)
            logger.debug(
                "load_teams_from_standings(): team %s %s was newly created" % (team.team_name, team.abbreviation))
        team.abbreviation = abbreviation
        team.team_name = team_name
        logger.info("loaded teams %s %s" % (team.team_name, team.abbreviation))
        team.save()


def __get_players_from_lineup(html):
    pool = BeautifulSoup(html)
    rows = pool.find_all('tr', 'pncPlayerRow')
    players = []
    for row in rows:
        slot = row.contents[0].string
        player_id = None
        if not row.contents[1].a:
            continue
        player_id = row.contents[1].a['playerid']
        players.append((slot, player_id))
    return players


def load_week_from_lineup(html, week, team):
    logger.info("creaing scorecard for week %s  team %s" % (str(week), team.team_name))
    scorecard, created = Scorecard.objects.get_or_create(team=team, week=week, actual=True)
    if not created:
        logger.warn("created lineup %s %s %s %d" % (team.league.espn_id, team.league.year, team.espn_id, week))
    players = __get_players_from_lineup(html)
    total_points = Decimal(0)
    for player_id in players:
        # logger.debug("loading score entry for player %s" % str(player_id))
        try:
            player = Player.objects.get(espn_id=player_id[1])
        except Player.DoesNotExist:
            logger.error("could not find player id %s" % str(player_id))
            raise
        try:
            points = ScoreEntry.objects.get(player=player, week=week,
                                            year=team.league.year).player_score_stats.default_points
        except ScoreEntry.DoesNotExist:
            logger.error("could not find scoreentry for player id %s" % str(player_id))
            raise

        slot = player_id[0]
        if slot != 'Bench':
            total_points = total_points + points
        source = team.get_source_for_player(player, week)
        ScorecardEntry.objects.create(scorecard=scorecard, player=player, slot=slot, points=points, source=source,
                                      team=team, week=week)
        logger.debug(
            "creating score card entry for player %s week %s points %s" % (player.espn_id, str(week), str(points)))
    scorecard.points = total_points
    scorecard.save()


def load_scores_from_game(league, week, html):
    logger.debug('load scores(): begin ... ')
    pool = BeautifulSoup(html)

    team_id_blocks = pool.find(id='teamInfos').find_all('a', href=re.compile(r'.*teamId.*'))
    team_ids = []
    for block in team_id_blocks:
        team_id = re.match('.*teamId=(\d*)', block['href']).group(1)
        team_ids.append(team_id)

    second_team_block = pool.find_all('div', {'style': 'clear: both;'})[1].previous_sibling
    first_team_block = second_team_block.previous_sibling

    team_blocks = [(team_ids[0], first_team_block), (team_ids[1], second_team_block)]

    for team_block in team_blocks:
        logger.debug("loading team_block %s " % team_block[0])
        team = Team.objects.get(league=league, espn_id=team_block[0])
        logger.debug("creating scorecard for team %s week %s" % (team.team_name, str(week)))
        scorecard = Scorecard.objects.create(team=team, week=week, actual=True)

        player_rows = team_block[1].find_all('tr', id=re.compile(r'plyr\d*'))
        total_points = Decimal(0)
        for player_row in player_rows:
            slot = player_row.td.string
            try:
                player_link = player_row.find('td', 'playertablePlayerName').a
            except AttributeError:
                logger.info("ignoring row %s " % ''.join(list(player_row.strings)))
                continue

            player_id = str(player_link['playerid'])
            points = player_row.find('td', 'appliedPoints').string
            if points == '--':
                points = '0'
            points = Decimal(points)
            name = str(player_link.string)
            position = str(player_link.next_sibling.split()[-1])
            try:
                player = Player.objects.get(name=name, position=position, espn_id=player_id)
            except Player.DoesNotExist:
                try:
                    player = Player.objects.get(name=name)
                    player.position = position
                    player.espn_id = player_id
                    player.save()
                except Player.DoesNotExist:
                    try:
                        player = Player.objects.get(espn_id=player_id)
                        player.position = position
                        player.other_name = name
                        player.save()
                    except Player.DoesNotExist:
                        logger.debug('creating new player %s' % name)
                        player = Player.objects.create(espn_id=player_id, name=name, position=position)

            source = team.get_source_for_player(player, week)
            ScorecardEntry.objects.create(scorecard=scorecard, player=player, slot=slot, points=points, source=source,
                                          week=week, team=team)
            if slot != 'Bench':
                total_points += points

        scorecard.points = total_points
        scorecard.save()


def clean_player(player_name):
    str(player_name)
    if player_name[-1] == '*':
        player_name = player_name[:-1]
    if player_name == 'Steve Smith Sr.':
        return "Steve Smith"
    return ' '.join(player_name.split()[:3])


def add_player(player_name, team, added, date):
    player_name = clean_player(player_name)
    try:
        player = Player.objects.get(name=player_name)
    except Player.DoesNotExist:
        logger.debug("creating player %s based on add/drop " % player_name)
        player = Player.objects.create(name=player_name)

    logger.debug("creating add drop entry %s %s %s %s" % (team.team_name, team.espn_id, player_name, date))
    AddDrop.objects.create(date=date, team=team, player=player, added=added)


def get_transaction_type(row):
    for string in row.contents[1].strings:
        if re.search(r'.*Add/Drop.*', string):
            logger.debug('got transaction type')
            return 'Add/Drop'
        if re.search(r'.*Add.*', string):
            logger.debug('got transaction type')
            return 'Add'
        if re.search(r'.*Draft.*', string):
            logger.debug('got draft type')
            return 'Draft'
        if re.search(r'.*Drop.*', string):
            logger.debug('got transaction type')
            return 'Drop'
        if re.search(r'.*Trade Processed.*', string):
            logger.debug('got trade processed')
            return 'Trade Processed'
        if re.search(r'.*Trade Upheld.*', string):
            logger.debug('got accpepted trade')
            return 'Trade Processed'
    logger.debug('returning another transaction type')
    return row.contents[1].contents[-1]


def load_transactions_from_translog(html, year, team):
    logger.debug("load_transactions_from_translog %s" % year)
    soup = BeautifulSoup(html)
    rows = soup.find_all('table')[0].find_all('tr')[3:]
    rows.reverse()
    draft_round = 0
    for row in rows:
        transaction_type = get_transaction_type(row)

        date_str = ' '.join(list(row.contents[0].strings))
        date = datetime.datetime.strptime(date_str, '%a, %b %d %I:%M %p')
        date = date.replace(year=int(year))
        logger.debug("date is %s" % str(date))
        logger.debug('tranaction type is %s' % transaction_type)

        if transaction_type == 'Draft':
            logger.debug("loading player %s" % str(row.contents[2]))
            draft_round = draft_round + 1
            try:
                player_name = row.contents[2].b.string
            except AttributeError:
                logger.error("row contents was %s" % str(row.contents[2]))

            player_name = clean_player(player_name)
            logger.debug("saving player %s" % (player_name))

            try:
                player = Player.objects.get(name=player_name)
            except Player.DoesNotExist:
                logger.debug("creating player %s based on draft " % player_name)
                player = Player.objects.create(name=player_name)

            draft_entry = DraftClaim(date=date, round=draft_round, player_added=player, team=team)
            draft_round = draft_round + 1
            draft_entry.save()
        elif transaction_type == 'Trade Processed':
            logger.debug('processing trade: %s' % str(row))
            trade_strings = ' '.join(row.contents[2].strings)
            trades = re.findall(r'(\S+?) traded (.+?) to (\S+?)( |$)', trade_strings)
            added_players = []
            removed_players = []
            other_team = None

            for trade in trades:
                logger.debug("handling trade %s " % str(trade))
                from_abbreviation = trade[0]
                to_abbreviation = trade[2]
                if to_abbreviation == team.abbreviation:
                    added = True
                    other_abbreviation = from_abbreviation
                else:
                    added = False
                    other_abbreviation = to_abbreviation

                if other_team is None:
                    try:
                        other_team = Team.objects.filter(abbreviation=other_abbreviation, league=team.league)[0]
                    except IndexError:
                        logger.error("cant find other team for abbreviation %s" % other_abbreviation)
                        continue
                logger.debug("for abbreviation %s found team %s" % (other_abbreviation, other_team.team_name))
                player_name = trade[1].split(',')[0].strip()
                player_name = clean_player(player_name)
                logger.debug("cleaned player name is %s added is %s " % (player_name, str(added)))

                try:
                    player = Player.objects.get(name=player_name)
                except Player.DoesNotExist:
                    logger.debug("creating player %s based on trade " % player_name)
                    player = Player.objects.create(name=player_name)
                if added:
                    added_players.append(player)
                else:
                    removed_players.append(player)
            logger.debug("creating trade entry added: %s removed %s" % (str(added_players), str(removed_players)))
            TradeEntry.objects.create_if_not_exists(date, team, other_team, added_players, removed_players)

        elif transaction_type == 'Add/Drop':
            content = list(row.contents[2].strings)
            logger.debug("processing add drop %s" % str(content))
            for i in range(0, len(content), 3):
                if (i + 2 >= len(content)):
                    break
                dropped = re.search('dropped', content[i]) is not None
                if dropped:
                    add_player(content[i + 1], team, False, date)
                else:
                    add_player(content[i + 1], team, True, date)
        elif transaction_type == 'Add' or transaction_type == 'Add (Waivers)':
            add_player(row.contents[2].contents[1].string, team, True, date)
        elif transaction_type == 'Drop':
            add_player(row.contents[2].contents[1].string, team, False, date)
        else:
            logger.debug("unsupported transactino type %s " % transaction_type)
            # logger.d("hm: %s" % str(row))




