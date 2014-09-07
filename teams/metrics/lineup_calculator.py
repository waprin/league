__author__ = 'bprin'

from functools import partial

def can_fill_slot(slot, entry):
    if slot == 'FLEX':
        return entry.player.position in ('RB', 'WR', 'TE')
    return slot == entry.player.position

def get_lineup_score(entries):
    print entries
    starters = filter(lambda entry: entry.slot != 'BENCH', entries)
    score = reduce(lambda points, entry: points + entry.points, starters, 0.0)
    return score


def calculate_optimal_lineup(entries):
    slots = [entry.slot for entry in entries]
    slots = filter(lambda slot: slot != 'BENCH', slots)
    try:
        i = slots.index('FLEX')
        slots.append(slots.pop(i))
    except ValueError:
        pass

    optimal_entries = []
    for slot in slots:
        available_players = filter(lambda entry: entry not in optimal_entries, entries)
        available_players = filter(partial(can_fill_slot, slot), available_players)
        print "available players: " + str(available_players) + " slot " + str(slot)
        best_player = max(available_players, key=lambda entry: entry.points)
        best_player.pk = None
        best_player.slot = slot
        best_player.save()
        print "best players: " + str(best_player)

        optimal_entries.append(best_player)
    return optimal_entries





