import csv
import json
import os

from tqdm import tqdm
from xlsxwriter.workbook import Workbook

from ai import MAX_DIFF, do_arima, do_lstm
from dataset import get_dataset, master_data_set as header
from game_information import TEAMS, get_team_info, CURRENT_SEASON, CURRENT_GAME_WEEK, CURRENT_SEASON_BEGINNING_ROUND, \
    SEASON_LENGTH, MIN_GAMES, MIN_SEASON_PPG, MIN_SEASON_GAME_PERCENTAGE, TEAM_WORTH, FREE_TRANSFERS, \
    PREDICT_BY_WEEKS, TRANSFER_COST
from solver import make_team

CURRENT_TEAM = {"Mark Flekken Flekken", "Norberto Murara Neto Neto",  # GKP
                "William Saliba Saliba", "Kyle Walker Walker", "Vitalii Mykolenko Mykolenko",
                "Alfie Doughty Doughty", "Virgil van Dijk Virgil",  # DEF
                "Gabriel Martinelli Silva Martinelli", "Phil Foden Foden", "Raheem Sterling Sterling",
                "Dejan Kulusevski Kulusevski", "Cole Palmer Palmer",  # MID
                "Erling Haaland Haaland", "Dominic Solanke Solanke", "Carlton Morris Morris"
                # FWD
                }

INJURIES = {
    "Anthony Martial Martial": 0,
    "Trent Alexander-Arnold Alexander-Arnold": 0,
    "Matheus Santos Carneiro Da Cunha Cunha": 0,
    "Thiago Emiliano da Silva T.Silva": 0.75,
    "Dominic Solanke Solanke": 0.75
}

RATIOS = {  # Last calibrated 2/23/2024
    'ARS': {'ARIMA': 1.0077061913, 'LSTM': 0.14271823337},
    'AVL': {'ARIMA': 0.85365521294, 'LSTM': 0.21879104629},
    'BOU': {'ARIMA': 1.2186678738, 'LSTM': 0},
    'BRE': {'ARIMA': 0, 'LSTM': 0.9649535542},
    'BHA': {'ARIMA': 1.0991567676, 'LSTM': 0.32347630172},
    'BUR': {'ARIMA': 0.75570225869, 'LSTM': 0.18944123827},
    'CHE': {'ARIMA': 0.63898650125, 'LSTM': 0.38168129446},
    'CRY': {'ARIMA': 0, 'LSTM': 1.1207278383},
    'EVE': {'ARIMA': 0.0000067406655668, 'LSTM': 1.0365344709},
    'FUL': {'ARIMA': 0, 'LSTM': 0.98013476722},
    'LIV': {'ARIMA': 0, 'LSTM': 1.130325545},
    'LUT': {'ARIMA': 0, 'LSTM': 1.5695609144},
    'MCI': {'ARIMA': 1.045006917, 'LSTM': 0.00000023464004252},
    'MUN': {'ARIMA': 0, 'LSTM': 0.90130935147},
    'NEW': {'ARIMA': 0.39507149829, 'LSTM': 0.46349462639},
    'NFO': {'ARIMA': 0.39597687024, 'LSTM': 0.96445330033},
    'SHU': {'ARIMA': 0.0000000967012693, 'LSTM': 1.1726216265},
    'TOT': {'ARIMA': 0, 'LSTM': 1.2497622938},
    'WHU': {'ARIMA': 0.00000016687520934, 'LSTM': 1.2209974888},
    'WOL': {'ARIMA': 0.66959318299, 'LSTM': 0.65584929232}}

PROCESS_ALL_PLAYERS = False
BUGGED_PLAYERS = []
HIDDEN_COLUMNS = ['GKP', 'DEF', 'MID', 'FWD', *TEAMS, 'ID', 'ARIMA', 'LSTM']
ALPHABET = [*"ABCDEFGHIJKLMNOPQRSTUVWXYZ"]
ID = header[0].index('ID')
PP = header[0].index('PP')
HEALTH = header[0].index('Health')
NEXT = header[0].index('NEXT')
PREV = header[0].index('PREV')
SELECTED = header[0].index('Selected')
FIRST_NAME = header[0].index('First Name')
SURNAME = header[0].index('Surname')
WEB_NAME = header[0].index('Web Name')

master_data_set = []
deleted_members = []
to_retry = []
points_data_set = {}
predict_by = {}


def init():
    for team in TEAMS:
        predict_by[team] = {'games': [], 'next': 0}

    for letter in [*"ABCDEFGHIJKLMNOPQRSTUVWXYZ"]:
        ALPHABET.append(f"A{letter}")

    global points_data_set, master_data_set
    points_data_set, master_data_set = get_dataset()

    get_predict_by()


def get_predict_by():
    teams = get_team_info()

    with open(f"../Fantasy-Premier-League/data/{CURRENT_SEASON}/fixtures.csv") as fixtures_file:
        fixture_reader = csv.DictReader(fixtures_file)

        fixture_reader = [fixture for fixture in fixture_reader if
                          fixture['event'] != '' and CURRENT_GAME_WEEK <= float(
                              fixture['event']) <= CURRENT_GAME_WEEK + PREDICT_BY_WEEKS - 1]

        for fixture in fixture_reader:
            predict_by[teams[int(fixture['team_h'])]['short_name']]['games'].append(int(fixture['team_h_difficulty']))
            predict_by[teams[int(fixture['team_a'])]['short_name']]['games'].append(int(fixture['team_a_difficulty']))

            if float(fixture['event']) == CURRENT_GAME_WEEK:
                predict_by[teams[int(fixture['team_h'])]['short_name']]['next'] += 1
                predict_by[teams[int(fixture['team_a'])]['short_name']]['next'] += 1

    make_training_set()


def make_training_set():
    global master_data_set

    found_previous = 0

    filename = f"./predictedData/{CURRENT_SEASON}/predictedData{CURRENT_GAME_WEEK}.json"
    if os.path.exists(filename):
        with open(filename, 'r') as file:
            master_data_set = json.load(file)

        print("Loaded predictions from file")

        for master in master_data_set:
            if len(master) != len(header[0]) or master == header[0]:
                continue

            player_name = f"{master[FIRST_NAME]} {master[SURNAME]} {master[WEB_NAME]}"
            if player_name in INJURIES:
                master[HEALTH] = INJURIES[player_name]
            else:
                master[HEALTH] = 1
            if player_name in CURRENT_TEAM:
                master[PREV] = 1
                found_previous += 1
            else:
                master[PREV] = 0

        if len(to_retry) == 0:
            make_prediction_file()
            return

    for _, player_data in tqdm(points_data_set.items()):
        if len(to_retry) > 0 and player_data['id'] not in to_retry:
            continue

        if player_data['id'] in deleted_members or player_data['id'] in BUGGED_PLAYERS:
            continue

        ts = []
        season_sum = 0
        num_games = 0
        total_games = 0

        player_name = f"{player_data['first_name']} {player_data['last_name']} {player_data['name']}"

        for dataset, data in player_data.items():
            if not dataset.startswith('GW'):
                continue

            total_games += 1
            ts.append(data['points'])

            round_num = int(dataset.replace("GW", ""))
            beginning_round = CURRENT_SEASON_BEGINNING_ROUND
            if CURRENT_GAME_WEEK == 1:
                beginning_round = CURRENT_SEASON_BEGINNING_ROUND - SEASON_LENGTH - CURRENT_GAME_WEEK

            if round_num >= beginning_round:
                season_sum += data['points']
                num_games += 1

        if not PROCESS_ALL_PLAYERS and (
                total_games < MIN_GAMES or season_sum < MIN_SEASON_PPG * num_games or num_games < (
                SEASON_LENGTH if CURRENT_GAME_WEEK == 1 else CURRENT_GAME_WEEK - 1) * MIN_SEASON_GAME_PERCENTAGE or len(
            predict_by[player_data['team']][
                'games']) < 1 or total_games < 2 or sum(ts[-PREDICT_BY_WEEKS:]) < PREDICT_BY_WEEKS * MIN_SEASON_PPG) and player_name not in CURRENT_TEAM:
            continue

        if season_sum <= 0 or len(predict_by[player_data['team']]['games']) == 0:
            arima_overall, arima_next = 0, 0
            lstm_overall, lstm_next = 0, 0
        else:
            try:
                arima_overall, arima_next = do_arima(ts, predict_by[player_data['team']])
                lstm_overall, lstm_next = do_lstm(player_data, predict_by[player_data['team']])
            except:
                BUGGED_PLAYERS.append(player_data['id'])
                continue

        if arima_overall != 0 and lstm_overall != 0 and (arima_overall / lstm_overall > MAX_DIFF or
                                                         lstm_overall / arima_overall > MAX_DIFF):
            BUGGED_PLAYERS.append(player_data['id'])
            continue

        if len(predict_by[player_data['team']]['games']) == 0:
            p = 0
            next_p = 0
        else:
            arima_ratio = RATIOS[player_data['team']]['ARIMA']
            lstm_ratio = RATIOS[player_data['team']]['LSTM']

            p = (arima_overall * arima_ratio) + (lstm_overall * lstm_ratio)
            next_p = (arima_next * arima_ratio) + (lstm_next * lstm_ratio)

        found = False
        for master in master_data_set:
            if master[ID] == player_data['id']:
                master.append(arima_overall)
                master.append(lstm_overall)
                master.append(p)
                master.append(next_p)
                if player_name in INJURIES:
                    master.append(INJURIES[player_name])
                else:
                    master.append(1)
                if player_name in CURRENT_TEAM:
                    master.append(1)
                    found_previous += 1
                else:
                    master.append(0)
                master.append(0)
                found = True
                break

        if not found:
            raise Exception(f"Couldn't find {player_data.id}")

    with open(filename, 'w') as dataset_file:
        json.dump(master_data_set, dataset_file, ensure_ascii=False, indent=4)
        print("Wrote Predicted Data")

    if found_previous == 15:
        print("Found all previous players!")
    else:
        print(f"Found only {found_previous} out of 15 previous players")

    make_prediction_file()


def make_prediction_file():
    global points_data_set, master_data_set

    workbook = Workbook(f"./Predictions/{CURRENT_SEASON}/Week {CURRENT_GAME_WEEK}.xlsx")
    sheet = workbook.add_worksheet()

    column_index = len(master_data_set[0]) + 1
    row_index = 1

    sheet.write_row(row_index, column_index, ["Total Points", "=SUMPRODUCT(Table1[Selected], Table1[PP])", "MAX"])

    row_index += 2

    sheet.write_row(row_index, column_index, ["Total Cost", "=SUMPRODUCT(Table1[Selected],Table1[Cost])", TEAM_WORTH])

    row_index += 2

    sheet.write_row(row_index, column_index, ["GKP", "=SUMPRODUCT(Table1[Selected],Table1[GKP])", 2])

    row_index += 1

    sheet.write_row(row_index, column_index, ["DEF", "=SUMPRODUCT(Table1[Selected],Table1[DEF])", 5])

    row_index += 1

    sheet.write_row(row_index, column_index, ["MID", "=SUMPRODUCT(Table1[Selected],Table1[MID])", 5])

    row_index += 1

    sheet.write_row(row_index, column_index, ["FWD", "=SUMPRODUCT(Table1[Selected],Table1[FWD])", 3])

    row_index += 2

    sheet.write_row(row_index, column_index, ["Transfers", "=SUMPRODUCT(Table1[Selected], -- (Table1[PREV] = 0))"])

    row_index += 1

    sheet.write_row(row_index, column_index, ["Free", FREE_TRANSFERS])

    row_index += 2

    sheet.write_row(row_index, column_index, ["Cost",
                                              f"=(({ALPHABET[column_index + 1]}{row_index - 2}-{ALPHABET[column_index + 1]}{row_index - 1})+ABS(({ALPHABET[column_index + 1]}{row_index - 2}-{ALPHABET[column_index + 1]}{row_index - 1})))/2*{TRANSFER_COST}"])

    row_index += 2

    sheet.write_row(row_index, column_index, ["Profit",
                                              f"={ALPHABET[column_index + 1]}{row_index - 13}-{ALPHABET[column_index + 1]}{row_index - 1}*{PREDICT_BY_WEEKS}"])

    row_index += 2

    for team_name in TEAMS:
        sheet.write_row(row_index, column_index, [team_name, f"=SUMPRODUCT(Table1[Selected],Table1[{team_name}])", 3])
        row_index += 1

    data = [player for player in master_data_set[1:] if len(player) == len(master_data_set[0])]

    best_team = make_team(data)

    found_selected = 0
    for player in data:
        player[PP] = f"={player[PP]}*{player[HEALTH]}"
        player[NEXT] = f"={player[NEXT]}*{player[HEALTH]}"

        if player in best_team:
            player[SELECTED] = 1
            found_selected += 1

    if found_selected == 15:
        print("Found all selected players!")
    else:
        print(f"Found only {found_selected} out of 15 selected players")

    columns = list(map(lambda x: {'header': x}, master_data_set[0]))

    sheet.add_table(f"A1:{ALPHABET[len(master_data_set[0]) - 1]}{len(data) + 1}",
                    {'data': data, 'columns': columns})

    for column_name in HIDDEN_COLUMNS:
        hidden_column_index = master_data_set[0].index(column_name)
        sheet.set_column(hidden_column_index, hidden_column_index, None, None, {'hidden': 1})

    for bugged_player in BUGGED_PLAYERS:
        print("Did not include player with id", bugged_player)
        print(points_data_set[bugged_player])

    workbook.close()


if __name__ == "__main__":
    init()
