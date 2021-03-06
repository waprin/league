from decimal import Decimal
import logging

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseRedirect
from django.contrib import messages
import logging
from django.shortcuts import redirect

from teams.management.commands.scrape_user import defer_espn_user_scrape
from teams.metrics.lineup_calculator import get_lineup_score
from teams.models import Scorecard, ScorecardEntry, Team, League, EspnUser, TeamReportCard, DraftClaim, TeamWeekScores, \
    AddDrop, TradeEntry, MailingList, BetaInvite, Player
import simplejson as json
from django.contrib.auth.models import User
from django.template import RequestContext, loader
import django_rq
from django.contrib.auth import authenticate, login, logout

from django.core.cache import cache

from teams.scraper.report_card import get_team_report_card_json, get_league_request_context
from teams.scraper.utils import num_weeks_before_date, real_num_weeks


logger = logging.getLogger(__name__)


def signin(request):
    try:
        email = request.POST['email']
        password = request.POST['password']
    except KeyError:
        template = loader.get_template('teams/landing.html')
        context = RequestContext(request)
        return HttpResponse(template.render(context))

    user = authenticate(username=email, password=password)
    if user is not None:
        if user.is_active:
            login(request, user)
            return redirect(show_all_leagues)
            # Redirect to a success page.
        else:
            logger.error("disabled account")
            template = loader.get_template('teams/landing.html')
            context = RequestContext(request, {
                'login_error': True
            })
            return HttpResponse(template.render(context))
    else:
        logger.error("invalid login")
        template = loader.get_template('teams/landing.html')
        context = RequestContext(request, {
            'login_error': True
        })
        return HttpResponse(template.render(context))
        # Return an 'invalid login' error message.


def signup(request):
    invite_code = request.POST.get('invite_code')
    try:
        logger.debug("getting invite '%s' " % (invite_code))
        invite = BetaInvite.objects.get(invite=invite_code)
        if invite.used:
            messages.add_message(request, messages.INFO, 'Invite Code Already Used')
            return HttpResponseRedirect("/register/")

    except BetaInvite.DoesNotExist:
        logger.debug("invalid invite code")
        messages.add_message(request, messages.INFO, 'Invalid Invite Code')
        return HttpResponseRedirect("/register/")

    password = request.POST.get('password')
    email = request.POST.get('email')
    espn_username = request.POST.get('espn_username')
    espn_password = request.POST.get('espn_password')
    allow_email = request.POST.get('allow_email')

    if not allow_email:
        allow_email = False

    if not password or not email or not espn_username or not espn_password:
        messages.add_message(request, messages.INFO, 'Missing Required Fields')
        return HttpResponseRedirect("/register/")

    if User.objects.filter(email=email).count() > 0:
        messages.add_message(request, messages.INFO, 'Email Already Registered')
        return HttpResponseRedirect("/register/")

    if len(EspnUser.objects.filter(username=espn_username)) > 0:
        messages.add_message(request, messages.INFO, 'ESPN Account Already Associated With Another FantasyBuff Account')
        return HttpResponseRedirect("/register/")

    User.objects.create_user(email, email, password)
    user = authenticate(username=email, password=password)
    if user is not None:
        if user.is_active:
            invite.used=True
            invite.save()

            login(request, user)
            espn_user = EspnUser.objects.create(user=request.user, username=espn_username, password=espn_password, loaded=False, allow_save=True, allow_email=allow_email)
            queue = django_rq.get_queue('low')
            queue.enqueue(defer_espn_user_scrape, espn_user)
            return redirect(show_all_leagues)
            # Redirect to a success page.
        else:
            logger.error("disabled account")
            template = loader.get_template('teams/register.html')
            context = RequestContext(request, {
            'registration_error': True
            })
            return HttpResponse(template.render(context))
    else:
        logger.error("invalid login")
        template = loader.get_template('teams/register.html')
        context = RequestContext(request, {
            'registration_error': True
        })
        return HttpResponse(template.render(context))


def logout_user(request):
    logout(request)
    return redirect(signin)


def make_nav_bar(league, team=None, week=None):
    bar = [{'value': 'Leagues Home', 'href': '/'}]
    bar.append({'value': league.name + ' ' + str(league.year), 'href': '/league/%s/%s/' % (league.espn_id, league.year)})
    if team:
        bar.append({'value': team.team_name + ' ' + str(league.year), 'href': '/league/%s/%s/%s/' % (league.espn_id, league.year, team.espn_id)})
    if week:
        bar.append({'value': str(week), 'href': '/league/%s/%s/%s/%d/' % (league.espn_id, league.year, team.espn_id, week)})

    return bar

@login_required
def show_team(request, espn_league_id, year, espn_team_id):
    logger.debug("entering show_team")
    league = League.objects.get(espn_id=espn_league_id, year=year)
    team = Team.objects.get(espn_id=espn_team_id, league=league)
    actual_weeks = list(Scorecard.objects.filter(team=team, actual=True))
    optimal_weeks = list(Scorecard.objects.filter(team=team, actual=False))
    actual_weeks.sort(key=lambda x: x.week)
    optimal_weeks.sort(key=lambda x: x.week)
    logger.debug("got all deltas")

    deltas = []

    weeks = []
    for i, week in enumerate(actual_weeks):
        optimal_points = optimal_weeks[i].points
        delta = optimal_points - week.points
        deltas.append(delta)
        weeks.append({"week": week.week,
                      "points": week.points,
                      "optimal_points": optimal_points,
                      "delta": delta,
        })
    average_delta = sum(deltas) / Decimal(len(deltas))
    logger.debug("got average delta %f" % average_delta)

    template = loader.get_template('teams/team.html')
    context = RequestContext(request, {
        'weeks': weeks,
        'average_delta': average_delta,
        'navigation': make_nav_bar(league, team)
    })

    return HttpResponse(template.render(context))

d = { 'QB': 1,
      'RB': 2,
       'WR': 3,
       'TE': 4,
       'FLEX': 5,
       'D/ST': 6,
       'K': 8,
       'Bench': 1000,
}


def order_entries(entries):
    def slot_key(entry):
        try:
            return d[entry.slot]
        except KeyError:
            return 500

    return sorted(entries, key=slot_key)


def show_week(request, espn_league_id, year, espn_team_id, week):
    logger.debug("entering show_week")
    league = League.objects.get(espn_id=espn_league_id, year=year)
    team = Team.objects.get(espn_id=espn_team_id, league=league)

    scorecard = Scorecard.objects.get(team=team, week=int(week), actual=True)
    scorecard_entries = list(ScorecardEntry.objects.filter(scorecard=scorecard))

    optimal_scorecard = Scorecard.objects.get(team=team, week=int(week), actual=False)
    optimal_entries = list(ScorecardEntry.objects.filter(scorecard=optimal_scorecard))

    actual_score = get_lineup_score(scorecard_entries)
    optimal_score = get_lineup_score(optimal_entries)

    logger.debug( "scorecard_entries " + str(scorecard_entries))
    scorecard_entries = order_entries(scorecard_entries)
    logger.debug("scorecard_entries sorted" + str(scorecard_entries))
    optimal_entries = order_entries(optimal_entries)

    template = loader.get_template('teams/week.html')
    context = RequestContext(request, {
        'scorecard_entries': scorecard_entries,
        'optimal_entries' : optimal_entries,
        'actual_score': actual_score,
        'optimal_score': optimal_score,
        'delta': optimal_score - actual_score,
        'navigation': make_nav_bar(league, team, int(week))
    })

    return HttpResponse(template.render(context))


def show_trade_week(request, league_id, year, team_id, week):
    league = League.objects.get(espn_id=league_id, year=year)
    team = Team.objects.get(espn_id=team_id, league=league)

    week = int(week)
    trade_transactions = TradeEntry.objects.get_before_week(team, week)

    all_players_added = []
    all_players_dropped = []
    for tt in trade_transactions:
        all_players_added += list(tt.players_added.all())
        all_players_dropped += list(tt.players_removed.all())
        logger.debug("going through trade transactions %s %s " % (str(all_players_added), str(all_players_dropped)))

    (all_players_added_scored, plusTotal) = ScorecardEntry.get_trade_value(all_players_added, week)
    (all_players_dropped_scored, minusTotal) = ScorecardEntry.get_trade_value(all_players_dropped, week)
    all_total = plusTotal - minusTotal
    template = loader.get_template('teams/trade.html')
    context = RequestContext(request, {
        'total_score': all_total,
        'added': all_players_added,
        'dropped': all_players_dropped,
        'added_scored': all_players_added_scored,
        'dropped_scored': all_players_dropped_scored,
    })
    return HttpResponse(template.render(context))


def show_waiver_week(request, league_id, year, team_id, week):
    logger.debug("entering show waiver week %s" % week)
    league = League.objects.get(espn_id=league_id, year=year)
    team = Team.objects.get(espn_id=team_id, league=league)

    week = int(week)
    add_drop_transactions = AddDrop.objects.get_before_week(team, week)

    total = 0
    added = []
    dropped = []
    for adt in add_drop_transactions:
        entries = ScorecardEntry.objects.filter(player=adt.player, scorecard__week=week,
                                                scorecard__team__league=team.league)
        if len(entries) > 0:
            entry = entries[0]
            if entry.slot != 'Bench':
                if adt.added:
                    total += entries[0].points
                    added.append(entries[0])
                else:
                    total -= entries[0].points
                    dropped.append(entries[0])

    template = loader.get_template('teams/waiver.html')
    context = RequestContext(request, {
        'total_score': total,
        'added': added,
        'dropped': dropped,
        'add_drop_transactions': add_drop_transactions,
        'team_name': team.team_name
    })
    return HttpResponse(template.render(context))


def show_draftscore_week(request, league_id, year, team_id, week):
    logger.debug("entering show_draftscore %s" % week)
    league = League.objects.get(espn_id=league_id, year=year)
    team = Team.objects.get(espn_id=team_id, league=league)

    drafts = DraftClaim.objects.filter(team=team)
    drafted_players = [draft.player_added for draft in drafts]
    for i, player in enumerate(drafted_players):
        scorecard_entry = ScorecardEntry.objects.filter(player=player, scorecard__week=int(week))[0]
        if scorecard_entry.slot != 'Bench':
            started = True
            total_points = scorecard_entry.points * 2
        else:
            started = False
            total_points = scorecard_entry.points

        drafted_players[i] = {'player': player, 'scorecard_entry': scorecard_entry, 'started': started,
                              'total_points': total_points}

    team_week_scores = TeamWeekScores.objects.get(week=int(week), team=team)
    logger.debug("rturning drafted player %s" % drafted_players)
    template = loader.get_template('teams/draftscore.html')
    context = RequestContext(request, {
        'total_score': team_week_scores.draft_score,
        'players': drafted_players,
        'navigation': make_nav_bar(league, team, int(week))
    })

    return HttpResponse(template.render(context))


def register(request):
    logger.info("loading register template")
    template = loader.get_template('teams/register.html')
    context = RequestContext(request)
    return HttpResponse(template.render(context))


@login_required()
def show_league(request, espn_id, year):
    league = League.objects.get(espn_id=espn_id, year=year)
    if not league.loaded:
        return HttpResponse("Sorry, league isn't loaded yet!")
    teams = Team.objects.filter(league=league)
    template = loader.get_template('teams/league.html')

    context = RequestContext(request, {
        'teams': teams,
        'league': league,
        'navigation': make_nav_bar(league),
    })
    return HttpResponse(template.render(context))


def _decode_list(data):
    rv = []
    for item in data:
        if isinstance(item, unicode):
            item = item.encode('utf-8')
        elif isinstance(item, list):
            item = _decode_list(item)
        elif isinstance(item, dict):
            item = _decode_dict(item)
        rv.append(item)
    return rv


def _decode_dict(data):
    rv = {}
    for key, value in data.iteritems():
        if isinstance(key, unicode):
            key = key.encode('utf-8')
        if isinstance(value, unicode):
            value = value.encode('utf-8')
        elif isinstance(value, list):
            value = _decode_list(value)
        elif isinstance(value, dict):
            value = _decode_dict(value)
        rv[key] = value
    return rv


from django.core.serializers.json import Serializer as Builtin_Serializer


class Serializer(Builtin_Serializer):
    def get_dump_object(self, obj):
        return self._current


@login_required()
def get_all_leagues_json(request, all=False):
    """
    if all and (not request.user.is_active or not request.user.is_superuser):
        return HttpResponseRedirect('/')
    """

    leagues = None
    if all:
        leagues = League.objects.all()
    else:
        espn_users = EspnUser.objects.filter(user=request.user)
        logger.info('adding league %s' % str(espn_users))
        for espn_user in espn_users:
            teams = Team.objects.filter(espn_user=espn_user, league__year='2014')
            leagues = [team.league for team in teams]

    all_accounts = []
    if leagues:
        data = Serializer().serialize(leagues, fields=('id', 'name', 'espn_id', 'year', 'loaded', 'failed',
                                                       'loading', 'pages_scraped', 'total_pages',
                                                       'league_loaded_finish_time', 'lineups_scrape_finish_time',
                                                       'calculating'))
        # data = serialize('json', leagues, fields=('name','espn_id', 'year', 'loaded', 'pages_scraped', 'total_pages'))
        data = json.loads(data)
        for i, league in enumerate(data):
            league['id'] = leagues[i].id
        all_accounts += data

    return HttpResponse(json.dumps(all_accounts), content_type="application/json")


def get_team_report_card_json_view(request, league_id, year, team_id):
    data = get_team_report_card_json(league_id, year, team_id)
    return HttpResponse(data, content_type="application/json")


def get_team_draft(request, league_id, year, team_id):
    league = League.objects.get(espn_id=league_id, year=year)
    team = Team.objects.get(league=league, espn_id=team_id)

    drafts = DraftClaim.objects.filter(team=team)
    drafted_players = [draft.player_added for draft in drafts]
    player_data = Serializer().serialize(drafted_players, fields=('name', 'position'))
    # data = json.dumps(drafted_players)
    return HttpResponse(player_data, content_type="application/json")


@login_required
def show_all_leagues(request):
    template = loader.get_template('teams/all_leagues.html')

    espn_users = EspnUser.objects.filter(user=request.user)
    espn_user = None
    if len(espn_users) > 0:
        espn_user = espn_users[0]

    context = RequestContext(request, {
        'navigation': ['Leagues'],
        'espn_user': espn_user,
        'global': False
    })
    return HttpResponse(template.render(context))


@login_required()
def show_global_leagues(request):
    """
    if not request.user.is_active or not request.user.is_superuser:
        return HttpResponseRedirect('/')
    """
    template = loader.get_template('teams/public_leagues.html')

    espn_users = EspnUser.objects.filter(user=request.user)
    espn_user = None
    if len(espn_users) > 0:
        espn_user = espn_users[0]

    context = RequestContext(request, {
        'navigation': ['Leagues'],
        'espn_user': espn_user,
    })
    return HttpResponse(template.render(context))


@login_required
def espn_refresh(request):
    try:
        username = request.POST['username']
        password = request.POST['password']
    except KeyError:
        return redirect(show_all_leagues)

        # logger.debug("creating espn user %s %s %s" % (request.user.username, username, password))
    #    espn_user = EspnUser.objects.filter(user=request.user)[0]

    try:
        espn_user = EspnUser.objects.filter(user=request.user, username=username)[0]
    except IndexError:
        espn_user = EspnUser.objects.create(user=request.user, username=username, password=password, loaded=False)

    espn_user.password = password
    espn_user.loaded = False
    espn_user.save()

    django_rq.enqueue(defer_espn_user_scrape, espn_user)
    return redirect(show_all_leagues)


def backbone(request, espn_id, year):
    league = League.objects.get(espn_id=espn_id, year=year)

    teams = Team.objects.filter(league=league)

    demo = False
    if not request.user.is_authenticated():
        if league.espn_id == '930248' and league.year == '2014':
            demo = True
            logger.info('using default user for demo')
            user = User.objects.get(username='waprin@gmail.com')
        else:
            return redirect('/signin')
    else:
        user = request.user

    logger.info("getting espn user for user %s" % str(user))

    espn_users = EspnUser.objects.filter(user=user)
    espn_user = None
    if len(espn_users) > 0:
        espn_user = espn_users[0]

    current_team = None
    for team in teams:
        if team.espn_user == espn_user:
            current_team = team

    cached_context = get_league_request_context(league)
    cached_context['demo'] = demo,
    cached_context['espn_user'] = espn_user
    cached_context['current_team'] = current_team,

    context = RequestContext(request, cached_context)
    template = loader.get_template('teams/dashboard.html')
    return HttpResponse(template.render(context))


def demo(request):
    return redirect('/leagues/espn/930248/2014/')


def mailing_list(request):
    email = request.POST.get('email')
    if email:
        messages.add_message(request, messages.INFO, 'Successfully Subscribed')
        MailingList.objects.get_or_create(email=email)
    return redirect('/')


def trade(request):
    league = League.objects.filter(espn_id='451385')
    maclin = Player.objects.get(name='Jeremy Maclin')
    trade = TradeEntry.objects.get(team__league=league, players_added=maclin)

    entries_for = []
    for player in trade.players_added.all():
        entries_for += list(ScorecardEntry.objects.filter(player=player, scorecard__team__league=league))

    week_after_trade = num_weeks_before_date(trade.date)
    current_weeks = real_num_weeks()

    week_range = range(week_after_trade, current_weeks + 1)
    weeks = []
    for week in week_range:
        entries_for = []
        for player in trade.players_added.all():
            entries_for += list(ScorecardEntry.objects.filter(player=player, week=week, scorecard__actual=True, scorecard__team__league=league))
        entries_against = []
        for player in trade.players_removed.all():
            entries_against += list(ScorecardEntry.objects.filter(player=player, week=week, scorecard__actual=True, scorecard__team__league=league))


        weeks.append(
            {
            'week': week,
            'entries_for': entries_for,
            'entries_against': entries_against,
            'points_for': trade.get_points_for_week(week),
            'points_against': trade.get_points_against_week(week)
            }
        )

    logger.debug("returning weeks %s" % (str(weeks)))
    cached_context = {
        'trade': trade,
        'start_week': week_after_trade,
        'weeks': weeks,
        'week_range': week_range
    }
    logger.debug("trade is %s" % trade.team.team_name)
    context = RequestContext(request, cached_context)
    template = loader.get_template('teams/trade_analyzer.html')
    return HttpResponse(template.render(context))