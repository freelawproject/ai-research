import re

federal_court_aliases = {
    "us_supreme_court": [
        "u.s. supreme court", "us supreme court", "supreme court of the united states", "scotus"
    ],
    
    "federal_claims": [
        "u.s. court of federal claims", "court of federal claims"
    ],
    "international_trade": [
        "u.s. court of international trade", "court of international trade"
    ],
    "tax_court": [
        "u.s. tax court", "us tax court", "tax court"
    ],
    "immigration_board": [
        "board of immigration appeals", "bia", "immigration"
    ],
    "veterans_claims": [
        "court of appeals for veterans claims", "veterans court", "veterans"
    ],
    "judicial_panel": [
        "judicial panel on multidistrict litigation", "jpml"
    ]
}

circuit_aliases = {
    "1st": ["1", "1st", "first", "1st circuit", "first circuit", "court of appeals for the first circuit"],
    "2nd": ["2", "2nd", "second", "2nd circuit", "second circuit", "court of appeals for the second circuit"],
    "3rd": ["3", "3rd", "third", "3rd circuit", "third circuit"],
    "4th": ["4", "4th", "fourth", "4th circuit", "fourth circuit"],
    "5th": ["5", "5th", "fifth", "5th circuit", "fifth circuit"],
    "6th": ["6", "6th", "sixth", "6th circuit", "sixth circuit"],
    "7th": ["7", "7th", "seventh", "7th circuit", "seventh circuit"],
    "8th": ["8", "8th", "eighth", "8th circuit", "eighth circuit"],
    "9th": ["9", "9th", "ninth", "9th circuit", "ninth circuit"],
    "10th": ["10", "10th", "tenth", "10th circuit", "tenth circuit"],
    "11th": ["11", "11th", "eleventh", "11th circuit", "eleventh circuit"],
    "dc": ["0", "dc circuit", "d.c. circuit", "district of columbia circuit"]
}

state_aliases = {
    "alabama": ["alabama", "al"],
    "alaska": ["alaska", "ak"],
    "arizona": ["arizona", "az"],
    "arkansas": ["arkansas", "ar"],
    "california": ["california", "ca"],
    "colorado": ["colorado", "co"],
    "connecticut": ["connecticut", "ct"],
    "delaware": ["delaware", "de"],
    "florida": ["florida", "fl"],
    "georgia": ["georgia", "ga"],
    "hawaii": ["hawaii", "hi"],
    "idaho": ["idaho", "id"],
    "illinois": ["illinois", "il"],
    "indiana": ["indiana", "in"],
    "iowa": ["iowa", "ia"],
    "kansas": ["kansas", "ks"],
    "kentucky": ["kentucky", "ky"],
    "louisiana": ["louisiana", "la"],
    "maine": ["maine", "me"],
    "maryland": ["maryland", "md"],
    "massachusetts": ["massachusetts", "ma"],
    "michigan": ["michigan", "mi"],
    "minnesota": ["minnesota", "mn"],
    "mississippi": ["mississippi", "ms"],
    "missouri": ["missouri", "mo"],
    "montana": ["montana", "mt"],
    "nebraska": ["nebraska", "ne"],
    "nevada": ["nevada", "nv"],
    "new hampshire": ["new hampshire", "nh"],
    "new jersey": ["new jersey", "nj"],
    "new mexico": ["new mexico", "nm"],
    "new york": ["new york", "ny"],
    "north carolina": ["north carolina", "nc"],
    "north dakota": ["north dakota", "nd"],
    "ohio": ["ohio", "oh"],
    "oklahoma": ["oklahoma", "ok"],
    "oregon": ["oregon", "or"],
    "pennsylvania": ["pennsylvania", "pa"],
    "rhode island": ["rhode island", "ri"],
    "south carolina": ["south carolina", "sc"],
    "south dakota": ["south dakota", "sd"],
    "tennessee": ["tennessee", "tn"],
    "texas": ["texas", "tx"],
    "utah": ["utah", "ut"],
    "vermont": ["vermont", "vt"],
    "virginia": ["virginia", "va"],
    "washington": ["washington", "wa"],
    "west virginia": ["west virginia", "wv"],
    "wisconsin": ["wisconsin", "wi"],
    "wyoming": ["wyoming", "wy"],
    "district of columbia": ["district of columbia", "dc", "d.c."],
    "micronesia": ["micronesia"],
    "virgin islands": ["virgin island"],
}

fjc_to_ch_circuits = {
    0: 5878,
    1: 5867,
    2: 5868,
    3: 5869,
    4: 5870,
    5: 5871,
    6: 5872,
    7: 5873,
    8: 5874,
    9: 5870,
    10: 5876,
    11: 5877,
    -8: None
}

ch_to_fjc_circuits = {
    5878: 0,
    5867: 1,
    5868: 2,
    5869: 3,
    5870: 4,
    5871: 5,
    5872: 6,
    5873: 7,
    5874: 8,
    5876: 10,
    5877: 11,
    None: -9
}

ch_to_fjc_court_lookup = {
    'Alaska state appellate court': 'ALASKA',
    'Alaska state supreme court': 'ALASKA',
    'Alaska state trial court': 'ALASKA',
    'Arizona state appellate court': 'ARIZONA',
    'Arizona state supreme court': 'ARIZONA',
    'Arizona state trial court': 'ARIZONA',
    'Arkansas state appellate court': 'KANSAS',
    'Arkansas state supreme court': 'KANSAS',
    'Arkansas state trial court': 'KANSAS',
    'Circuit Court of the District of Columbia (defunct)': 'DISTRICT OF COLUMBIA',
    'Circuit Court of the District of Columbia USCC (defunct)': 'DISTRICT OF COLUMBIA',
    'Colorado state appellate court': 'COLORADO',
    'Colorado state supreme court': 'COLORADO',
    'Colorado state trial court': 'COLORADO',
    'Connecticut state appellate court': 'CONNECTICUT',
    'Connecticut state supreme court': 'CONNECTICUT',
    'Connecticut state trial court': 'CONNECTICUT',
    'Court of Claims (defunct)': 'COURT OF CLAIMS',
    'Delaware state appellate court': 'DELAWARE',
    'Delaware state supreme court': 'DELAWARE',
    'Delaware state trial court': 'DELAWARE',
    'District Court of Guam': 'GUAM',
    'District Court of the Virgin Islands': 'VIRGIN ISLANDS',
    'District of Alaska': 'ALASKA',
    'District of Arizona': 'ARIZONA',
    'District of Arkansas (defunct)': 'KANSAS',
    'District of Colorado': 'COLORADO',
    'District of Columbia state appellate court': 'DISTRICT OF COLUMBIA',
    'District of Columbia state supreme court': 'DISTRICT OF COLUMBIA',
    'District of Columbia state trial court': 'DISTRICT OF COLUMBIA',
    'District of Connecticut': 'CONNECTICUT',
    'District of Delaware': 'DELAWARE',
    'District of District of Columbia': 'DISTRICT OF COLUMBIA',
    'District of Hawaii': 'HAWAII',
    'District of Idaho': 'IDAHO',
    'District of Kansas': 'KANSAS',
    'District of Maine': 'MAINE',
    'District of Maryland': 'MARYLAND',
    'District of Massachusetts': 'MASSACHUSETTS',
    'District of Minnesota': 'MINNESOTA',
    'District of Montana': 'MONTANA',
    'District of Nebraska': 'NEBRASKA',
    'District of Nevada': 'NEVADA',
    'District of New Hampshire': 'NEW HAMPSHIRE',
    'District of New Jersey': 'NEW JERSEY',
    'District of New Mexico': 'NEW MEXICO',
    'District of North Dakota': 'NORTH DAKOTA',
    'District of Oregon': 'OREGON',
    'District of Puerto Rico': 'PUERTO RICO',
    'District of Rhode Island': 'RHODE ISLAND',
    'District of South Carolina': 'SOUTH CAROLINA',
    'District of South Dakota': 'SOUTH DAKOTA',
    'District of Utah': 'UTAH',
    'District of Vermont': 'VERMONT',
    'District of Wyoming': 'WYOMING',
    'Eastern District of Arkansas': 'KANSAS',
    'Eastern District of South Carolina (defunct)': 'SOUTH CAROLINA',
    'Guam state appellate court': 'GUAM',
    'Guam state supreme court': 'GUAM',
    'Guam state trial court': 'GUAM',
    'Hawaii state appellate court': 'HAWAII',
    'Hawaii state supreme court': 'HAWAII',
    'Hawaii state trial court': 'HAWAII',
    'Idaho state appellate court': 'IDAHO',
    'Idaho state supreme court': 'IDAHO',
    'Idaho state trial court': 'IDAHO',
    'Kansas state appellate court': 'KANSAS',
    'Kansas state supreme court': 'KANSAS',
    'Kansas state trial court': 'KANSAS',
    'Maine state appellate court': 'MAINE',
    'Maine state supreme court': 'MAINE',
    'Maine state trial court': 'MAINE',
    'Maryland state appellate court': 'MARYLAND',
    'Maryland state supreme court': 'MARYLAND',
    'Maryland state trial court': 'MARYLAND',
    'Massachusetts state appellate court': 'MASSACHUSETTS',
    'Massachusetts state supreme court': 'MASSACHUSETTS',
    'Massachusetts state trial court': 'MASSACHUSETTS',
    'Minnesota state appellate court': 'MINNESOTA',
    'Minnesota state supreme court': 'MINNESOTA',
    'Minnesota state trial court': 'MINNESOTA',
    'Montana state appellate court': 'MONTANA',
    'Montana state supreme court': 'MONTANA',
    'Montana state trial court': 'MONTANA',
    'Nebraska state appellate court': 'NEBRASKA',
    'Nebraska state supreme court': 'NEBRASKA',
    'Nebraska state trial court': 'NEBRASKA',
    'Nevada state appellate court': 'NEVADA',
    'Nevada state supreme court': 'NEVADA',
    'Nevada state trial court': 'NEVADA',
    'New Hampshire state appellate court': 'NEW HAMPSHIRE',
    'New Hampshire state supreme court': 'NEW HAMPSHIRE',
    'New Hampshire state trial court': 'NEW HAMPSHIRE',
    'New Jersey state appellate court': 'NEW JERSEY',
    'New Jersey state supreme court': 'NEW JERSEY',
    'New Jersey state trial court': 'NEW JERSEY',
    'New Mexico state appellate court': 'NEW MEXICO',
    'New Mexico state supreme court': 'NEW MEXICO',
    'New Mexico state trial court': 'NEW MEXICO',
    'North Dakota state appellate court': 'NORTH DAKOTA',
    'North Dakota state supreme court': 'NORTH DAKOTA',
    'North Dakota state trial court': 'NORTH DAKOTA',
    'Oregon state appellate court': 'OREGON',
    'Oregon state supreme court': 'OREGON',
    'Oregon state trial court': 'OREGON',
    'Puerto Rico state appellate court': 'PUERTO RICO',
    'Puerto Rico state supreme court': 'PUERTO RICO',
    'Puerto Rico state trial court': 'PUERTO RICO',
    'Rhode Island state appellate court': 'RHODE ISLAND',
    'Rhode Island state supreme court': 'RHODE ISLAND',
    'Rhode Island state trial court': 'RHODE ISLAND',
    'South Carolina state appellate court': 'SOUTH CAROLINA',
    'South Carolina state supreme court': 'SOUTH CAROLINA',
    'South Carolina state trial court': 'SOUTH CAROLINA',
    'South Dakota state appellate court': 'SOUTH DAKOTA',
    'South Dakota state supreme court': 'SOUTH DAKOTA',
    'South Dakota state trial court': 'SOUTH DAKOTA',
    'Utah state appellate court': 'UTAH',
    'Utah state supreme court': 'UTAH',
    'Utah state trial court': 'UTAH',
    'Vermont state appellate court': 'VERMONT',
    'Vermont state supreme court': 'VERMONT',
    'Vermont state trial court': 'VERMONT',
    'Western District of Arkansas': 'KANSAS',
    'Wyoming state appellate court': 'WYOMING',
    'Wyoming state supreme court': 'WYOMING',
    'Wyoming state trial court': 'WYOMING',
    'Central District of California': 'CALIFORNIA CENTRAL',
    'Central District of Illinois': 'ILLINOIS CENTRAL',
    'Eastern District of Arkansas': 'ARKANSAS EASTERN',
    'Eastern District of California': 'CALIFORNIA EASTERN',
    'Eastern District of Kentucky': 'KENTUCKY EASTERN',
    'Eastern District of Louisiana': 'LOUISIANA EASTERN',
    'Eastern District of Michigan': 'MICHIGAN EASTERN',
    'Eastern District of Missouri': 'MISSOURI EASTERN',
    'Eastern District of New York': 'NEW YORK EASTERN',
    'Eastern District of North Carolina': 'NO. CAROLINA EASTERN',
    'Eastern District of Oklahoma': 'OKLAHOMA EASTERN',
    'Eastern District of Pennsylvania': 'PENNSYLVANIA EASTERN',
    'Eastern District of Tennessee': 'TENNESSEE EASTERN',
    'Eastern District of Texas': 'TEXAS EASTERN',
    'Eastern District of Virginia': 'VIRGINIA EASTERN',
    'Eastern District of Washington': 'WASHINGTON EASTERN',
    'Eastern District of Wisconsin': 'WISCONSIN EASTERN',
    'Middle District of Alabama': 'ALABAMA MIDDLE',
    'Middle District of Florida': 'FLORIDA MIDDLE',
    'Middle District of Georgia': 'GEORGIA MIDDLE',
    'Middle District of Louisiana': 'LOUISIANA MIDDLE',
    'Middle District of North Carolina': 'NO. CAROLINA MIDDLE',
    'Middle District of Pennsylvania': 'PENNSYLVANIA MIDDLE',
    'Middle District of Tennessee': 'TENNESSEE MIDDLE',
    'Northern District of Alabama': 'ALABAMA NORTHERN',
    'Northern District of California': 'CALIFORNIA NORTHERN',
    'Northern District of Florida': 'FLORIDA NORTHERN',
    'Northern District of Georgia': 'GEORGIA NORTHERN',
    'Northern District of Illinois': 'ILLINOIS NORTHERN',
    'Northern District of Indiana': 'INDIANA NORTHERN',
    'Northern District of Iowa': 'IOWA NORTHERN',
    'Northern District of Mississippi': 'MISSISSIPPI NORTHERN',
    'Northern District of New York': 'NEW YORK NORTHERN',
    'Northern District of Ohio': 'OHIO NORTHERN',
    'Northern District of Oklahoma': 'OKLAHOMA NORTHERN',
    'Northern District of Texas': 'TEXAS NORTHERN',
    'Northern District of West Virginia': 'W. VIRGINIA NORTHERN',
    'Southern District of Alabama': 'ALABAMA SOUTHERN',
    'Southern District of California': 'CALIFORNIA SOUTHERN',
    'Southern District of Florida': 'FLORIDA SOUTHERN',
    'Southern District of Georgia': 'GEORGIA SOUTHERN',
    'Southern District of Illinois': 'ILLINOIS SOUTHERN',
    'Southern District of Indiana': 'INDIANA SOUTHERN',
    'Southern District of Iowa': 'IOWA SOUTHERN',
    'Southern District of Mississippi': 'MISSISSIPPI SOUTHERN',
    'Southern District of New York': 'NEW YORK SOUTHERN',
    'Southern District of Ohio': 'OHIO SOUTHERN',
    'Southern District of Texas': 'TEXAS SOUTHERN',
    'Southern District of West Virginia': 'W. VIRGINIA SOUTHERN',
    'Western District of Arkansas': 'ARKANSAS WESTERN',
    'Western District of Kentucky': 'KENTUCKY WESTERN',
    'Western District of Louisiana': 'LOUISIANA WESTERN',
    'Western District of Michigan': 'MICHIGAN WESTERN',
    'Western District of Missouri': 'MISSOURI WESTERN',
    'Western District of New York': 'NEW YORK WESTERN',
    'Western District of North Carolina': 'NO. CAROLINA WESTERN',
    'Western District of Oklahoma': 'OKLAHOMA WESTERN',
    'Western District of Pennsylvania': 'PENNSYLVANIA WESTERN',
    'Western District of Tennessee': 'TENNESSEE WESTERN',
    'Western District of Texas': 'TEXAS WESTERN',
    'Western District of Virginia': 'VIRGINIA WESTERN',
    'Western District of Washington': 'WASHINGTON WESTERN',
    'Western District of Wisconsin': 'WISCONSIN WESTERN'
}

fjc_lower_court_lookup = {
    '---0': 'DC CIRCUIT',
    '---1': 'FIRST CIRCUIT',
    '---2': 'SECOND CIRCUIT',
    '---3': 'THIRD CIRCUIT',
    '---4': 'FOURTH CIRCUIT',
    '---5': 'FIFTH CIRCUIT',
    '---6': 'SIXTH CIRCUIT',
    '---7': 'SEVENTH CIRCUIT',
    '---8': 'EIGHTH CIRCUIT',
    '---9': 'NINTH CIRCUIT',
    '--10': 'TENTH CIRCUIT',
    '--11': 'ELEVENTH CIRCUIT',
    '--12': 'INTERNATIONAL TRADE',
    '--13': 'FEDERAL CIRCUIT',
    '--14': 'COURT OF CLAIMS',
    '--15': 'VETERANS APPEALS',
    '0090': 'DISTRICT OF COLUMBIA',
    '0100': 'MAINE',
    '0101': 'MASSACHUSETTS',
    '0102': 'NEW HAMPSHIRE',
    '0103': 'RHODE ISLAND',
    '0104': 'PUERTO RICO',
    '0205': 'CONNECTICUT',
    '0206': 'NEW YORK NORTHERN',
    '0207': 'NEW YORK EASTERN',
    '0208': 'NEW YORK SOUTHERN',
    '0209': 'NEW YORK WESTERN',
    '0210': 'VERMONT',
    '0311': 'DELAWARE',
    '0312': 'NEW JERSEY',
    '0313': 'PENNSYLVANIA EASTERN',
    '0314': 'PENNSYLVANIA MIDDLE',
    '0315': 'PENNSYLVANIA WESTERN',
    '0391': 'VIRGIN ISLANDS',
    '0416': 'MARYLAND',
    '0417': 'NO. CAROLINA EASTERN',
    '0418': 'NO. CAROLINA MIDDLE',
    '0419': 'NO. CAROLINA WESTERN',
    '0420': 'SOUTH CAROLINA',
    '0422': 'VIRGINIA EASTERN',
    '0423': 'VIRGINIA WESTERN',
    '0424': 'W. VIRGINIA NORTHERN',
    '0425': 'W. VIRGINIA SOUTHERN',
    '053L': 'LOUISIANA EASTERN',
    '053N': 'LOUISIANA MIDDLE',
    '0536': 'LOUISIANA WESTERN',
    '0537': 'MISSISSIPPI NORTHERN',
    '0538': 'MISSISSIPPI SOUTHERN',
    '0539': 'TEXAS NORTHERN',
    '0540': 'TEXAS EASTERN',
    '0541': 'TEXAS SOUTHERN',
    '0542': 'TEXAS WESTERN',
    '0643': 'KENTUCKY EASTERN',
    '0644': 'KENTUCKY WESTERN',
    '0645': 'MICHIGAN EASTERN',
    '0646': 'MICHIGAN WESTERN',
    '0647': 'OHIO NORTHERN',
    '0648': 'OHIO SOUTHERN',
    '0649': 'TENNESSEE EASTERN',
    '0650': 'TENNESSEE MIDDLE',
    '0651': 'TENNESSEE WESTERN',
    '0752': 'ILLINOIS NORTHERN',
    '0753': 'ILLINOIS CENTRAL',
    '0754': 'ILLINOIS SOUTHERN',
    '0755': 'INDIANA NORTHERN',
    '0756': 'INDIANA SOUTHERN',
    '0757': 'WISCONSIN EASTERN',
    '0758': 'WISCONSIN WESTERN',
    '0860': 'ARKANSAS EASTERN',
    '0861': 'ARKANSAS WESTERN',
    '0862': 'IOWA NORTHERN',
    '0863': 'IOWA SOUTHERN',
    '0864': 'MINNESOTA',
    '0865': 'MISSOURI EASTERN',
    '0866': 'MISSOURI WESTERN',
    '0867': 'NEBRASKA',
    '0868': 'NORTH DAKOTA',
    '0869': 'SOUTH DAKOTA',
    '097-': 'ALASKA',
    '0970': 'ARIZONA',
    '0971': 'CALIFORNIA NORTHERN',
    '0972': 'CALIFORNIA EASTERN',
    '0973': 'CALIFORNIA CENTRAL',
    '0974': 'CALIFORNIA SOUTHERN',
    '0975': 'HAWAII',
    '0976': 'IDAHO',
    '0977': 'MONTANA',
    '0978': 'NEVADA',
    '0979': 'OREGON',
    '0980': 'WASHINGTON EASTERN',
    '0981': 'WASHINGTON WESTERN',
    '0993': 'GUAM',
    '0994': 'NORTHERN MARIANAS',
    '1082': 'COLORADO',
    '1083': 'KANSAS',
    '1084': 'NEW MEXICO',
    '1085': 'OKLAHOMA NORTHERN',
    '1086': 'OKLAHOMA EASTERN',
    '1087': 'OKLAHOMA WESTERN',
    '1088': 'UTAH',
    '1089': 'WYOMING',
    '1111': 'TEST DISTRCIT',
    '1126': 'ALABAMA NORTHERN',
    '1127': 'ALABAMA MIDDLE',
    '1128': 'ALABAMA SOUTHERN',
    '1129': 'FLORIDA NORTHERN',
    '113A': 'FLORIDA MIDDLE',
    '113C': 'FLORIDA SOUTHERN',
    '113E': 'GEORGIA NORTHERN',
    '113G': 'GEORGIA MIDDLE',
    '113J': 'GEORGIA SOUTHERN',
}

scotus_circuit_id_map = {
    'columbia': 5878,
    'dc': 5878,
    'd.c.': 5878,
    'first': 5867,
    '1st': 5867,
    'second': 5868,
    '2nd': 5868,
    'third': 5869,
    '3rd': 5869,
    'fourth': 5870,
    '4th': 5870,
    'fifth': 5871,
    '5th': 5871,
    'sixth': 5872,
    '6th': 5872,
    'seventh': 5873,
    '7th': 5873,
    'eighth': 5874,
    '8th': 5874,
    'ninth': 5870,
    '9th': 5870,
    'tenth': 5876,
    '10th': 5876,
    'eleventh': 5877,
    '11th': 5877,
    'fed.': 5879,
    'fed': 5879,
    'federal': 5879,
    'fISCR': 5880,
    'foreign intelligence surveillance court of review': 5880,
}

def normalize_circuit_or_federal_name(name, aliases):
    name = name.strip().lower()
    for normalized, variants in aliases.items():
        if any(variant in name for variant in variants):
            return normalized
    return None

def normalize_state_name(name):
    name = name.strip().lower()

    for normalized, variants in state_aliases.items():
        for variant in variants:
            if len(variant) <= 2:
                # Match exact abbreviations like "ny", "ca"
                if name == variant:
                    return normalized
            else:
                # Match with word boundaries
                # e.g., \bnew york\b or \bdistrict of columbia\b
                pattern = rf'\b{re.escape(variant)}\b'
                if re.search(pattern, name):
                    return normalized
    return None


def ch_matches_scotus_lower_court(docket, scotus_lower_court_name):
    if docket.court.court_type and docket.court.court_type.id in [5887, 6267, 6266, 5888]: # district of X, X state trial court, X state appellate court, X state supreme court
        norm_ch = normalize_state_name(docket.court.name) 
        norm_scotus = normalize_state_name(scotus_lower_court_name)
    elif docket.court.court_type and docket.court.court_type.id in [5889]: #circuit names
        norm_ch = normalize_circuit_or_federal_name(docket.court.name, circuit_aliases) 
        norm_scotus = normalize_circuit_or_federal_name(scotus_lower_court_name, circuit_aliases)
    else:
        norm_ch = normalize_circuit_or_federal_name(docket.court.name, federal_court_aliases) 
        norm_scotus = normalize_circuit_or_federal_name(scotus_lower_court_name, federal_court_aliases)
    
    # double check circuit
    norm_lower_circuit = normalize_circuit_or_federal_name(docket.court.name, circuit_aliases)
    norm_scotus_circuit = normalize_circuit_or_federal_name(scotus_lower_court_name, circuit_aliases)
    return norm_ch is not None and norm_scotus is not None and norm_ch == norm_scotus and norm_scotus_circuit == norm_lower_circuit

def fjc_matches_scotus_lower_court(fjc_court, scotus_lower_court_name):
    norm_scotus = normalize_circuit_or_federal_name(scotus_lower_court_name, circuit_aliases)
    norm_fjc = normalize_circuit_or_federal_name(fjc_court, circuit_aliases)
    return norm_fjc is not None and norm_scotus is not None and norm_fjc == norm_scotus 

def ch_matches_fjc_lower_court(docket, fjc_row):
    fjc_court = fjc_lower_court_lookup[(str(fjc_row['DCIRC']).zfill(2) + str(fjc_row['DDIST']).zfill(2))]
    ch_court = ch_to_fjc_court_lookup[docket.court.name] if docket.court.name in ch_to_fjc_court_lookup else None 
    if ch_court and fjc_court == ch_court:
        return True
    else: 
        return False

def normalize_fjc_to_ch(ddocket):
    normalized = (str(ddocket).zfill(7))
    fjc_year = int(normalized[:2])
    if fjc_year > 40:
        fjc_year = fjc_year + 1900
    else:
        fjc_year = fjc_year + 2000
    fjc_filing_number = normalized[-5:]
    return fjc_year, fjc_filing_number