# Only keep the columns we care about
COLUMNS = {
    "SOURCE": "Source of data (1971-2007 or 2008-2025).",
    "CIRCUIT": "Circuit court in which the appeal was filed.",
    "DOCKET": "Case number assigned by the Clerk of Court upon docketing of the appeal.",
    "DKTDATE": "Date the case was docketed in the Court of Appeals.",
    "DDIST": "District/Bankruptcy court in which the case was originally filed.",
    "DDOCKET": "Docket number assigned to the case in the District/ Bankruptcy court.",
    "DDKTDATE": "Date the case was docketed in the district/bankruptcy court.",
    "APPDATE": "Date the notice of appeal was filed in the lower court.",
    "JUDGDATE": "Date the final judgment/decree/order to an appeal was entered to announce the decision of the court.",
    "APPTYPE": "Type of appeal at time of filing.",
    "DISP": "Type of disposition action taken by the court to terminate the appeal.",
    "OUTCOME": "Type of disposition when the decision is based on the merits of the case and the appeal is not an original proceeding.",
    "PROCTERM": "Type of disposition when the decision is not based on the merits of the case and the appeal is not an original proceeding.",
    "METHOD": "Type of disposition that is not based on the merits of the case and the appeal is not an original proceeding.",
    "PUBSTAT": "Opinion or order prepared by the court to dispose of the appeal.",
    "NEWSYTRM": "AOUSC statistical year in which the appeal was terminated, Valid through FY95."
    }


# To map the CIRCUIT to CIRCUIT_STR
CIRCUIT_values = {
    0: "District of Columbia Circuit",
    1: "First Circuit",
    2: "Second Circuit",
    3: "Third Circuit",
    4: "Fourth Circuit",
    5: "Fifth Circuit",
    6: "Sixth Circuit",
    7: "Seventh Circuit",
    8: "Eighth Circuit",
    9: "Ninth Circuit",
    10: "Tenth Circuit",
    11: "Eleventh Circuit"
}

CIRCUIT_MAP = {
    0: "cadc",
    1: "ca1",
    2: "ca2",
    3: "ca3",
    4: "ca4",
    5: "ca5",
    6: "ca6",
    7: "ca7",
    8: "ca8",
    9: "ca9",
    10: "ca10",
    11: "ca11"
}

CIRCUITS = list(set(CIRCUIT_MAP.values()))


# To map the DDIST to DDIST_STR, separate codes by year ranges
DDIST_values_old = {
    "00": "Maine",
    "01": "Massachusetts",
    "02": "New Hampshire",
    "03": "Rhode Island",
    "04": "Puerto Rico",
    "05": "Connecticut",
    "06": "New York - Northern",
    "07": "New York - Eastern",
    "08": "New York - Southern",
    "09": "New York - Western",
    "10": "Vermont",
    "11": "Delaware",
    "12": "New Jersey",
    "13": "Pennsylvania - Eastern",
    "14": "Pennsylvania - Middle",
    "15": "Pennsylvania - Western",
    "16": "Maryland",
    "17": "North Carolina - Eastern",
    "18": "North Carolina - Middle",
    "19": "North Carolina - Western",
    "20": "South Carolina",
    "22": "Virginia - Eastern",
    "23": "Virginia - Western",
    "24": "West Virginia - Northern",
    "25": "West Virginia - Southern",
    "26": "Alabama - Northern",
    "27": "Alabama - Middle",
    "28": "Alabama - Southern",
    "29": "Florida - Northern",
    "3A": "Florida - Middle",
    "3C": "Florida - Southern",
    "3E": "Georgia - Northern",
    "3G": "Georgia - Middle",
    "3J": "Georgia - Southern",
    "3L": "Louisiana - Eastern",
    "3N": "Louisiana - Middle",
    "36": "Louisiana - Western",
    "37": "Mississippi - Northern",
    "38": "Mississippi - Southern",
    "39": "Texas - Northern",
    "40": "Texas - Eastern",
    "41": "Texas - Southern",
    "42": "Texas - Western",
    "43": "Kentucky - Eastern",
    "44": "Kentucky - Western",
    "45": "Michigan - Eastern",
    "46": "Michigan - Western",
    "47": "Ohio - Northern",
    "48": "Ohio - Southern",
    "49": "Tennessee - Eastern",
    "50": "Tennessee - Middle",
    "51": "Tennessee - Western",
    "52": "Illinois - Northern",
    "53": "Illinois - Central",
    "54": "Illinois - Southern",
    "55": "Indiana - Northern",
    "56": "Indiana - Southern",
    "57": "Wisconsin - Eastern",
    "58": "Wisconsin - Western",
    "60": "Arkansas - Eastern",
    "61": "Arkansas - Western",
    "62": "Iowa - Northern",
    "63": "Iowa - Southern",
    "64": "Minnesota",
    "65": "Missouri - Eastern",
    "66": "Missouri - Western",
    "67": "Nebraska",
    "68": "North Dakota",
    "69": "South Dakota",
    "7-": "Alaska",
    "70": "Arizona",
    "71": "California - Northern",
    "72": "California - Eastern",
    "73": "California - Central",
    "74": "California - Southern",
    "75": "Hawaii",
    "76": "Idaho",
    "77": "Montana",
    "78": "Nevada",
    "79": "Oregon",
    "80": "Washington - Eastern",
    "81": "Washington - Western",
    "82": "Colorado",
    "83": "Kansas",
    "84": "New Mexico",
    "85": "Oklahoma - Northern",
    "86": "Oklahoma - Eastern",
    "87": "Oklahoma - Western",
    "88": "Utah",
    "89": "Wyoming",
    "90": "District of Columbia",
    "91": "Virgin Islands",
    "92": "Canal Zone",
    "93": "Guam",
    "94": "Northern Mariana Islands"
}

DDIST_MAP_OLD = {
    "00": "med",            # Maine
    "01": "mad",            # Massachusetts
    "02": "nhd",            # New Hampshire
    "03": "rid",            # Rhode Island
    "04": "prd",            # Puerto Rico
    "05": "ctd",            # Connecticut
    "06": "nynd",           # New York - Northern
    "07": "nyed",           # New York - Eastern
    "08": "nysd",           # New York - Southern
    "09": "nywd",           # New York - Western
    "10": "vtd",            # Vermont
    "11": "ded",            # Delaware
    "12": "njd",            # New Jersey
    "13": "paed",           # Pennsylvania - Eastern
    "14": "pamd",           # Pennsylvania - Middle
    "15": "pawd",           # Pennsylvania - Western
    "16": "mdd",            # Maryland
    "17": "nced",           # North Carolina - Eastern
    "18": "ncmd",           # North Carolina - Middle
    "19": "ncwd",           # North Carolina - Western
    "20": "scd",            # South Carolina
    "22": "vaed",           # Virginia - Eastern
    "23": "vawd",           # Virginia - Western
    "24": "wvnd",           # West Virginia - Northern
    "25": "wvsd",           # West Virginia - Southern
    "26": "alnd",           # Alabama - Northern
    "27": "almd",           # Alabama - Middle
    "28": "alsd",           # Alabama - Southern
    "29": "flnd",           # Florida - Northern
    "3A": "flmd",           # Florida - Middle
    "3C": "flsd",           # Florida - Southern
    "3E": "gand",           # Georgia - Northern
    "3G": "gamd",           # Georgia - Middle
    "3J": "gasd",           # Georgia - Southern
    "3L": "laed",           # Louisiana - Eastern
    "3N": "lamd",           # Louisiana - Middle
    "36": "lawd",           # Louisiana - Western
    "37": "msnd",           # Mississippi - Northern
    "38": "mssd",           # Mississippi - Southern
    "39": "txnd",           # Texas - Northern
    "40": "txed",           # Texas - Eastern
    "41": "txsd",           # Texas - Southern
    "42": "txwd",           # Texas - Western
    "43": "kyed",           # Kentucky - Eastern
    "44": "kywd",           # Kentucky - Western
    "45": "mied",           # Michigan - Eastern
    "46": "miwd",           # Michigan - Western
    "47": "ohnd",           # Ohio - Northern
    "48": "ohsd",           # Ohio - Southern
    "49": "tned",           # Tennessee - Eastern
    "50": "tnmd",           # Tennessee - Middle
    "51": "tnwd",           # Tennessee - Western
    "52": "ilnd",           # Illinois - Northern
    "53": "ilcd",           # Illinois - Central
    "54": "ilsd",           # Illinois - Southern
    "55": "innd",           # Indiana - Northern
    "56": "insd",           # Indiana - Southern
    "57": "wied",           # Wisconsin - Eastern
    "58": "wiwd",           # Wisconsin - Western
    "60": "ared",           # Arkansas - Eastern
    "61": "arwd",           # Arkansas - Western
    "62": "iand",           # Iowa - Northern
    "63": "iasd",           # Iowa - Southern
    "64": "mnd",            # Minnesota
    "65": "moed",           # Missouri - Eastern
    "66": "mowd",           # Missouri - Western
    "67": "ned",            # Nebraska
    "68": "ndd",            # North Dakota
    "69": "sdd",            # South Dakota
    "7-": "akd",            # Alaska
    "70": "azd",            # Arizona
    "71": "cand",           # California - Northern
    "72": "caed",           # California - Eastern
    "73": "cacd",           # California - Central
    "74": "casd",           # California - Southern
    "75": "hid",            # Hawaii
    "76": "idd",            # Idaho
    "77": "mtd",            # Montana
    "78": "nvd",            # Nevada
    "79": "ord",            # Oregon
    "80": "waed",           # Washington - Eastern
    "81": "wawd",           # Washington - Western
    "82": "cod",            # Colorado
    "83": "ksd",            # Kansas
    "84": "nmd",            # New Mexico
    "85": "oknd",           # Oklahoma - Northern
    "86": "oked",           # Oklahoma - Eastern
    "87": "okwd",           # Oklahoma - Western
    "88": "utd",            # Utah
    "89": "wyd",            # Wyoming
    "90": "dcd",            # District of Columbia
    "91": "vid",            # Virgin Islands
    "92": "canalzoned",     # Canal Zone
    "93": "gud",            # Guam
    "94": "nmid"            # Northern Mariana Islands
}

DDIST_values_new = {
    "---0": "DC CIRCUIT",
    "---1": "FIRST CIRCUIT",
    "---2": "SECOND CIRCUIT",
    "---3": "THIRD CIRCUIT",
    "---4": "FOURTH CIRCUIT",
    "---5": "FIFTH CIRCUIT",
    "---6": "SIXTH CIRCUIT",
    "---7": "SEVENTH CIRCUIT",
    "---8": "EIGHTH CIRCUIT",
    "---9": "NINTH CIRCUIT",
    "--10": "TENTH CIRCUIT",
    "--11": "ELEVENTH CIRCUIT",
    "--12": "INTERNATIONAL TRADE",
    "--13": "FEDERAL CIRCUIT",
    "--14": "COURT OF CLAIMS",
    "--15": "VETERANS APPEALS",
    "0090": "DISTRICT OF COLUMBIA",
    "0100": "MAINE",
    "0101": "MASSACHUSETTS",
    "0102": "NEW HAMPSHIRE",
    "0103": "RHODE ISLAND",
    "0104": "PUERTO RICO",
    "0205": "CONNECTICUT",
    "0206": "NEW YORK NORTHERN",
    "0207": "NEW YORK EASTERN",
    "0208": "NEW YORK SOUTHERN",
    "0209": "NEW YORK WESTERN",
    "0210": "VERMONT",
    "0311": "DELAWARE",
    "0312": "NEW JERSEY",
    "0313": "PENNSYLVANIA EASTERN",
    "0314": "PENNSYLVANIA MIDDLE",
    "0315": "PENNSYLVANIA WESTERN",
    "0391": "VIRGIN ISLANDS",
    "0416": "MARYLAND",
    "0417": "NO. CAROLINA EASTERN",
    "0418": "NO. CAROLINA MIDDLE",
    "0419": "NO. CAROLINA WESTERN",
    "0420": "SOUTH CAROLINA",
    "0422": "VIRGINIA EASTERN",
    "0423": "VIRGINIA WESTERN",
    "0424": "W. VIRGINIA NORTHERN",
    "0425": "W. VIRGINIA SOUTHERN",
    "053L": "LOUISIANA EASTERN",
    "053N": "LOUISIANA MIDDLE",
    "0536": "LOUISIANA WESTERN",
    "0537": "MISSISSIPPI NORTHERN",
    "0538": "MISSISSIPPI SOUTHERN",
    "0539": "TEXAS NORTHERN",
    "0540": "TEXAS EASTERN",
    "0541": "TEXAS SOUTHERN",
    "0542": "TEXAS WESTERN",
    "0643": "KENTUCKY EASTERN",
    "0644": "KENTUCKY WESTERN",
    "0645": "MICHIGAN EASTERN",
    "0646": "MICHIGAN WESTERN",
    "0647": "OHIO NORTHERN",
    "0648": "OHIO SOUTHERN",
    "0649": "TENNESSEE EASTERN",
    "0650": "TENNESSEE MIDDLE",
    "0651": "TENNESSEE WESTERN",
    "0752": "ILLINOIS NORTHERN",
    "0753": "ILLINOIS CENTRAL",
    "0754": "ILLINOIS SOUTHERN",
    "0755": "INDIANA NORTHERN",
    "0756": "INDIANA SOUTHERN",
    "0757": "WISCONSIN EASTERN",
    "0758": "WISCONSIN WESTERN",
    "0860": "ARKANSAS EASTERN",
    "0861": "ARKANSAS WESTERN",
    "0862": "IOWA NORTHERN",
    "0863": "IOWA SOUTHERN",
    "0864": "MINNESOTA",
    "0865": "MISSOURI EASTERN",
    "0866": "MISSOURI WESTERN",
    "0867": "NEBRASKA",
    "0868": "NORTH DAKOTA",
    "0869": "SOUTH DAKOTA",
    "097-": "ALASKA",
    "0970": "ARIZONA",
    "0971": "CALIFORNIA NORTHERN",
    "0972": "CALIFORNIA EASTERN",
    "0973": "CALIFORNIA CENTRAL",
    "0974": "CALIFORNIA SOUTHERN",
    "0975": "HAWAII",
    "0976": "IDAHO",
    "0977": "MONTANA",
    "0978": "NEVADA",
    "0979": "OREGON",
    "0980": "WASHINGTON EASTERN",
    "0981": "WASHINGTON WESTERN",
    "0993": "GUAM",
    "0994": "NORTHERN MARIANAS",
    "1082": "COLORADO",
    "1083": "KANSAS",
    "1084": "NEW MEXICO",
    "1085": "OKLAHOMA NORTHERN",
    "1086": "OKLAHOMA EASTERN",
    "1087": "OKLAHOMA WESTERN",
    "1088": "UTAH",
    "1089": "WYOMING",
    "1111": "TEST DISTRCIT", # TO BE REMOVED
    "1126": "ALABAMA NORTHERN",
    "1127": "ALABAMA MIDDLE",
    "1128": "ALABAMA SOUTHERN",
    "1129": "FLORIDA NORTHERN",
    "113A": "FLORIDA MIDDLE",
    "113C": "FLORIDA SOUTHERN",
    "113E": "GEORGIA NORTHERN",
    "113G": "GEORGIA MIDDLE",
    "113J": "GEORGIA SOUTHERN"
}

DDIST_MAP_NEW = {
    "---0": "cadc",     # DC CIRCUIT
    "---1": "ca1",      # FIRST CIRCUIT
    "---2": "ca2",      # SECOND CIRCUIT
    "---3": "ca3",      # THIRD CIRCUIT
    "---4": "ca4",      # FOURTH CIRCUIT
    "---5": "ca5",      # FIFTH CIRCUIT
    "---6": "ca6",      # SIXTH CIRCUIT
    "---7": "ca7",      # SEVENTH CIRCUIT
    "---8": "ca8",      # EIGHTH CIRCUIT
    "---9": "ca9",      # NINTH CIRCUIT
    "--10": "ca10",     # TENTH CIRCUIT
    "--11": "ca11",     # ELEVENTH CIRCUIT
    "--12": "cit",      # INTERNATIONAL TRADE
    "--13": "cafc",     # FEDERAL CIRCUIT
    "--14": "cc",       # COURT OF CLAIMS
    "--15": "cavc",     # VETERANS APPEALS
    "0090": "dcd",      # DISTRICT OF COLUMBIA
    "0100": "med",      # MAINE
    "0101": "mad",      # MASSACHUSETTS
    "0102": "nhd",      # NEW HAMPSHIRE
    "0103": "rid",      # RHODE ISLAND
    "0104": "prd",      # PUERTO RICO
    "0205": "ctd",      # CONNECTICUT
    "0206": "nynd",     # NEW YORK NORTHERN
    "0207": "nyed",     # NEW YORK EASTERN
    "0208": "nysd",     # NEW YORK SOUTHERN
    "0209": "nywd",     # NEW YORK WESTERN
    "0210": "vtd",      # VERMONT
    "0311": "ded",      # DELAWARE
    "0312": "njd",      # NEW JERSEY
    "0313": "paed",     # PENNSYLVANIA EASTERN
    "0314": "pamd",     # PENNSYLVANIA MIDDLE
    "0315": "pawd",     # PENNSYLVANIA WESTERN
    "0391": "vid",      # VIRGIN ISLANDS
    "0416": "mdd",      # MARYLAND
    "0417": "nced",     # NO. CAROLINA EASTERN
    "0418": "ncmd",     # NO. CAROLINA MIDDLE
    "0419": "ncwd",     # NO. CAROLINA WESTERN
    "0420": "scd",      # SOUTH CAROLINA
    "0422": "vaed",     # VIRGINIA EASTERN
    "0423": "vawd",     # VIRGINIA WESTERN
    "0424": "wvnd",     # W. VIRGINIA NORTHERN
    "0425": "wvsd",     # W. VIRGINIA SOUTHERN
    "053L": "laed",     # LOUISIANA EASTERN
    "053N": "lamd",     # LOUISIANA MIDDLE
    "0536": "lawd",     # LOUISIANA WESTERN
    "0537": "msnd",     # MISSISSIPPI NORTHERN
    "0538": "mssd",     # MISSISSIPPI SOUTHERN
    "0539": "txnd",     # TEXAS NORTHERN
    "0540": "txed",     # TEXAS EASTERN
    "0541": "txsd",     # TEXAS SOUTHERN
    "0542": "txwd",     # TEXAS WESTERN
    "0643": "kyed",     # KENTUCKY EASTERN
    "0644": "kywd",     # KENTUCKY WESTERN
    "0645": "mied",     # MICHIGAN EASTERN
    "0646": "miwd",     # MICHIGAN WESTERN
    "0647": "ohnd",     # OHIO NORTHERN
    "0648": "ohsd",     # OHIO SOUTHERN
    "0649": "tned",     # TENNESSEE EASTERN
    "0650": "tnmd",     # TENNESSEE MIDDLE
    "0651": "tnwd",     # TENNESSEE WESTERN
    "0752": "ilnd",     # ILLINOIS NORTHERN
    "0753": "ilcd",     # ILLINOIS CENTRAL
    "0754": "ilsd",     # ILLINOIS SOUTHERN
    "0755": "innd",     # INDIANA NORTHERN
    "0756": "insd",     # INDIANA SOUTHERN
    "0757": "wied",     # WISCONSIN EASTERN
    "0758": "wiwd",     # WISCONSIN WESTERN
    "0860": "ared",     # ARKANSAS EASTERN
    "0861": "arwd",     # ARKANSAS WESTERN
    "0862": "iand",     # IOWA NORTHERN
    "0863": "iasd",     # IOWA SOUTHERN
    "0864": "mnd",      # MINNESOTA
    "0865": "moed",     # MISSOURI EASTERN
    "0866": "mowd",     # MISSOURI WESTERN
    "0867": "ned",      # NEBRASKA
    "0868": "ndd",      # NORTH DAKOTA
    "0869": "sdd",      # SOUTH DAKOTA
    "097-": "akd",      # ALASKA
    "0970": "azd",      # ARIZONA
    "0971": "cand",     # CALIFORNIA NORTHERN
    "0972": "caed",     # CALIFORNIA EASTERN
    "0973": "cacd",     # CALIFORNIA CENTRAL
    "0974": "casd",     # CALIFORNIA SOUTHERN
    "0975": "hid",      # HAWAII
    "0976": "idd",      # IDAHO
    "0977": "mtd",      # MONTANA
    "0978": "nvd",      # NEVADA
    "0979": "ord",      # OREGON
    "0980": "waed",     # WASHINGTON EASTERN
    "0981": "wawd",     # WASHINGTON WESTERN
    "0993": "gud",      # GUAM
    "0994": "nmid",     # NORTHERN MARIANAS
    "1082": "cod",      # COLORADO
    "1083": "ksd",      # KANSAS
    "1084": "nmd",      # NEW MEXICO
    "1085": "oknd",     # OKLAHOMA NORTHERN
    "1086": "oked",     # OKLAHOMA EASTERN
    "1087": "okwd",     # OKLAHOMA WESTERN
    "1088": "utd",      # UTAH
    "1089": "wyd",      # WYOMING
    "1126": "alnd",     # ALABAMA NORTHERN
    "1127": "almd",     # ALABAMA MIDDLE
    "1128": "alsd",     # ALABAMA SOUTHERN
    "1129": "flnd",     # FLORIDA NORTHERN
    "113A": "flmd",     # FLORIDA MIDDLE
    "113C": "flsd",     # FLORIDA SOUTHERN
    "113E": "gand",     # GEORGIA NORTHERN
    "113G": "gamd",     # GEORGIA MIDDLE
    "113J": "gasd"      # GEORGIA SOUTHERN
}

DISTRICTS = list(set(DDIST_MAP_OLD.values()).union(set(DDIST_MAP_NEW.values())))


# To map the APPTYPE to APPTYPE_STR, combined codes for old and new data
APPTYPE_MAP = {
    1: "Administrative Review",
    2: "Administrative Enforcement",
    3: "Civil, U.S.",
    4: "Civil, Private",
    5: "Criminal (prior to SY88)",
    6: "Original Proceeding",
    7: "Emergency Court - Civil (prior to SY81)",
    8: "Emergency Court - Criminal (prior to SY81)",
    9: "Bankruptcy (prior to SY81)",
    10: "Bankruptcy Appeal from from Bankruptcy court",
    11: "Bankruptcy Appeal from a Bankruptcy Appellate Panel (BAP)",
    12: "Bankruptcy Appeal From District Court",
    13: "Guideline case - general",
    14: "Pre-guideline case or other",
    15: "Guideline case - sentence only",
    16: "Guideline case - conviction only",
    17: "Guideline case - sentence and conviction",
    18: "Criminal case other than 14, 15, 16 and 17 above (new value 2008)",
    19: "Direct Criminal Appeal",
    20: "Interlocutory",
    21: "Post-conviction",
    22: "Miscellaneous case",
    -8: "Missing"
}


# To map different dispositions to a unified disposition column DISP_UNIFIED
DISP_MAP = {
    1: "OUTCOME",
    2: "OUTCOME",
    3: "OUTCOME",
    4: "PROCTERM",
    5: "METHOD"
}


# To map the DISP to DISP_STR, separate codes by year ranges
# Some of the old DISP maps are invalid as values assigned in the dataset do not exist in the codebook
# We therefore only used DISP_MAP_NEW for all data
#DISP_MAP_71_80 = {
#    1: "After Oral Argument",
#    2: "Without Oral Argument",
#    -8: "Missing"
#}

#DISP_MAP_81_84 = {
#    1: "After Oral Argument",
#    2: "After Submission on Briefs",
#    3: "Other Judicial Action",
#    4: "Without Judicial Action",
#    -8: "Missing"
#}

DISP_MAP_NEW = {
    1: "After Oral Argument",
    2: "After Submission Without Oral Argument (Argument Waived)",
    3: "After Submission Without Oral Argument (Court Rule)",
    4: "After Other Judicial Action",
    5: "Without Judicial Action",
    -8: "Missing"
}


# To map the OUTCOME to OUTCOME_STR, combined codes for old and new data
OUTCOME_MAP = {
    1: "Affirmed",
    2: "Reversed, Vacated",
    3: "Affirmed in part and reversed/vacated in part",
    4: "Dismissed",
    5: "Dismissed",
    6: "Remanded",
    7: "Other",
    9: "Certificate of appealability",
    -8: "Missing"
}


# To map the PROCTERM to PROCTERM_STR, separate codes by year ranges
PROCTERM_MAP_71_DEC_75 = {
    1: "Consent Decree",
    2: "Affirmed",
    3: "Reversed",
    4: "Dismissed",
    5: "Cross Appeal or Consolidation",
    6: "Other",
    7: "Special Dismissal",
    8: "Error",
    -8: "Missing"
}

PROCTERM_MAP_JAN_76_84 = {
    1: "Consent Decree",
    2: "Affirmed",
    3: "Reversed",
    4: "Dismissed",
    5: "Out of Court Settlement",
    6: "Other",
    7: "Special Dismissal",
    8: "Error",
    -8: "Missing"
}

PROCTERM_MAP_NEW = {
    1: "Jurisdictional Defects",
    2: "F.R.A.P. 42",
    3: "Settlement Program",
    4: "No longer in use", 
    5: "Default",
    6: "CPC Denial",
    7: "Transferred",
    8: "Dismissed/Other",
    9: "Certificate of appealability",
    -8: "Missing"
}


# To map the METHOD to METHOD_STR, separate codes by year ranges
METHOD_MAP_71_84 = {
    1: "Written Opinion",
    2: "Memorandum Decision",
    3: "Decided From the Bench",
    4: "By Court Order",
    5: "By Consent",
    6: "Other",
    -8: "Missing"
}

METHOD_MAP_NEW = {
    1: "F.R.A.P. 42",
    2: "No longer in use",
    3: "Default",
    4: "Other",
    -8: "Missing"
}


# To map the PUBSTAT to PUBSTAT_STR, separate codes by year ranges
# Some of the old PUBSTAT maps are invalid as values assigned in the dataset do not exist in the codebook
# We therefore only used PUBSTAT_MAP_NEW for all data
# PUBSTAT_MAP_71_DEC_75 = {
#     1: "Missing",
#     2: "By Judge",
#     3: "Per Curiam",
#     -8: "Missing"
# }

# PUBSTAT_MAP_76_80 = {
#     1: "Published, Other",
#     2: "Published, Signed",
#     3: "Published, Reasons Stated",
#     4: "Unpublished, Other",
#     5: "Unpublished, Signed",
#     6: "Unpublished, Reasons Stated",
#     -8: "Missing"
# }

# PUBSTAT_MAP_81_84 = {
#     1: "Published, Other",
#     2: "Published, Signed",
#     3: "Published, Unsigned",
#     4: "Unpublished, Other",
#     5: "Unpublished, Signed",
#     6: "Unpublished, Unsigned",
#     -8: "Missing"
# }

PUBSTAT_MAP_NEW = {
    1: "Unpublished, Oral",
    2: "Published, Written, Signed",
    3: "Unpublished, Written, Signed",
    4: "Published, Written, Unsigned Reasoned",
    5: "Unpublished, Written, Unsigned Reasoned",
    6: "Published, Written, Unsigned without Comment",
    7: "Unpublished, Written, Unsigned without Comment",
    -8: "Missing",
    0: "Missing" # Some records have 0 as PUBSTAT even though it's not in the codebook
}

