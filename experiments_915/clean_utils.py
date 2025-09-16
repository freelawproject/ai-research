import re

def normalize_dashes(text: str) -> str:
    """Convert en & em dash(es), double hyphens, and similar to single hyphen(s)

    :param text: The text to convert
    :return: the better text
    """
    # Simple variables b/c in monospace code, you can't see the difference
    # otherwise.
    normal_dash = "-"
    en_dash = "–"
    em_dash = "—"
    hyphen = "‐"
    non_breaking_hyphen = "‑"
    figure_dash = "‒"
    horizontal_bar = "―"
    long_dash = "——"
    text = re.sub(r"--+", normal_dash, text) # handle double dashes
    return re.sub(
        rf"[{en_dash}{em_dash}{hyphen}{non_breaking_hyphen}{figure_dash}{horizontal_bar}{long_dash}]",
        normal_dash,
        text,
    )


def string_clean(s):
    # Remove trailing numbers followed by underscores
    s = re.sub(r'_\d+$', '', s) # eg. '17-1222_1', '1222_2'

    # Remove leading dash
    s = re.sub(r'^-+', '', s) # eg. '-17-1222', '-1222'

    # Remove trailing dash
    s = re.sub(r'-+$', '', s) # eg. '17-1222-', '1222-'

    # Remove trailing period
    s = re.sub(r'\.+$', '', s) # eg. '17-1222.', '1222...'

    # Remove all trailing letters (with optional space or optional dash leading the letter and optional period trailing the letter) after nnnn or yy-nnnn
    s = re.sub(r'(\d{1,6}|\d{2}-\d{1,6})(\s?\-?[a-zA-Z]{1,4}\.?)$', r'\1', s) # eg. '17-1222 a', '17-1222a', '17-1222-a', '17-1222a.', '1222aa', '1222a.'

    return s


def prelim_clean(s):

    # Lowercase and trim
    s = s.lower().strip()

    # Normalize dashes
    s = normalize_dashes(s)

    # Remove space around dashes
    s = re.sub(r'\s*-\s*', '-', s)

    # Fix typos
    s= s.replace("bocket", "docket").replace("dcoket", "docket").replace("socket", "docket")

    # If "(Trial Group" or "USDC" or "August Term" or "October Term" or "BRB", or "INS", "Tax Court" or "Tax Ct." or "Adv. No. " or "Adv. Pro." or "Bankruptcy" or "Opposition" or "D.C. Docket" or "BIA Agency" or "BIA" or "Agency" or "NLRB" (case-insensitive) appears, remove everything after it
    s = re.sub(r'\(.*?\btrial\s+group\b.*$', '', s) # eg. 'Nos. 79-1795, 79-1800, and 79-1802 (Trial Group O)'
    s = re.sub(r'\busdc\b.*$', '', s) # eg. '04-20917; USDC 4:03-CR-331-6, 4:03-CR-330-7'
    s = re.sub(r'\baugust\s+term\b.*$', '', s) # eg. '17-3550; August Term, 2018'
    s = re.sub(r'\boctober\s+term\b.*$', '', s) # eg. '17-3550; October Term, 2018'
    s = re.sub(r'\bbrb\b.*$', '', s) # eg. '12-71891; BRB 11-0533'
    s = re.sub(r'\bins\b.*$', '', s) # eg. '02-70048, INS A92-440-540'
    s = re.sub(r'\btax\s+court\b.*$', '', s) # eg. '94-70183; Tax Court 16266-91'
    s = re.sub(r'\btax\s+ct\.?\b.*$', '', s) # eg. '12-70259; Tax Ct. 20894-05'
    s = re.sub(r'\badv\.?\s+no\.?\b.*$', '', s) # eg. '05-16173, 05-16389, 05-16406, 05-16554. Adv. No. 98-2313-RCJ'
    s = re.sub(r'\badv\.?\s+pro\.?\b.*$', '', s) # eg. '13-16020; Adv. Pro. 10-90146'
    s = re.sub(r'\bbankruptcy\b.*$', '', s) # eg. '80-5569; Bankruptcy 79-03326-M'
    s = re.sub(r'\bopposition\b.*$', '', s) # eg. '06-1148; Opposition 91124847'
    s = re.sub(r'\bd\.c\.?\s+docket\b.*$', '', s) # eg. '05-11019; D.C. Docket 04-00102-CV-2'
    s = re.sub(r'\bbia\s+agency\b.*$', '', s) # eg. '05-11791; BIA Agency A95-551-857 & A95-551-858'
    s = re.sub(r'\bbia\b.*$', '', s) # eg. '05-11125; BIA A96-442-100'
    s = re.sub(r'\bagency\b.*$', '', s) # eg. '05-11847; Agency A95-886-023 & A95-886-024'
    s = re.sub(r'\bnlrb\b.*$', '', s) # eg. '03-16340; NLRB 12-CA-23237'

    # If "Dockets" (case-insensitive, singular/plural) appears in the middle, remove everything before it
    s = re.sub(r'^.*?\bdockets?\b', '', s) # eg. '284, 285, 321; Dockets 25524, 25525, 25577'

    # Remove special chars
    special_chars = [" et al.",
                     "-cr", " cr", "-pr", " pr", "-cv", "-ag", "-ev", "-bk", "(nac)", "c.a. ", "ca ", "c. a. ", 
                     "(l)", "(lead)", "(lead case)", "(sealed case)", "consolidated", "cons. ", 
                     "summary calendar", "non-argument calendar", "argument calendar", "conference calendar", 
                     "(a)", "(c)", "(con)", "(con.)", "(con.)", "ccon)", "(c0n)", "kcon)", "ccon)", "(copn)", 
                     "(xap)", "(xap.)", "kxap)", "(ag)", "(0", "(1)", "kl)", "cl)", 
                     "crlead", "crcon", "agcon", "nch ", 
                     "docket ", "dockets ", "case ", "appeal ", "civ. ", 
                     "ño. ", "no. ", "no.. ", 'no! ', 'wo. ', "no, ", "no ", "no: ", "na. ", "ne.", "nos. ", "no.[s]: ", "nos, ", "nos.: ", "noi ", "mo. ",
                     "misc. ", " misc.", "misc ", " misc", "mise. ", ", orig.", ", original", " original", " orig.", " orig", 
                     " : ", "*", ".pdf"]
    for char in special_chars:
        s = s.replace(char, '')

    # Replace "and", "&", ";", "with", "w/", "c/w" with commas
    s = re.sub(r'(and|&|;|with|w/|c/w)(?=\W|$)', ',', s)

    # Split the string by commas and apply string_clean to each part, then join back with commas
    parts = [p.strip() for p in re.split(r',', s) if p.strip()]
    # if all parts are integers, keep the original string without cleaning, to preserve thousand separators
    if all(re.fullmatch(r'\d{1,6}', p) for p in parts):
        s = s.strip() # remove all leading and trailing spaces
    else:
        s = ', '.join([string_clean(p) for p in parts])

    return s


def is_valid_delimiteds_format(s):
    """
    Returns True if s contains docket numbers separated by commas, where the first part is in yy-nnnn format and all subsequent parts are nnnn.
    """
    parts = [p.strip() for p in re.split(r',', s) if p.strip()]
    if len(parts) < 2:
        return False

    # Check first part is yy-nnnn
    if not re.fullmatch(r'\d{2}-\d{1,6}', parts[0]):
        return False

    # Check all subsequent parts are nnnn, have the same length as the nnnn part of the first part,
    # and start with the same first digit as the first part or the first digit + 1
    first_nnnn = parts[0].split('-')[1]
    first_digit = first_nnnn[0]
    allowed_digits = {first_digit, str(int(first_digit) + 1)}
    for part in parts[1:]:
        if not re.fullmatch(r'\d{1,6}', part) or len(part) != len(first_nnnn):
            return False
        if part[0] not in allowed_digits:
            return False

    return True


def is_valid_slash_format(s):
    """
    Returns True if the string contains docket numbers separated by slashes,
    where the part immediately before the first slash is in yy-nnnn format,
    and all subsequent parts after each slash are nnnn or yy-nnnn of the same length as the part before the slash.
    """
    # Split by comma first, then by slash
    parts = []
    for chunk in re.split(r'[,\s;]+', s):
        parts.extend([p.strip() for p in re.split(r'/', chunk) if p.strip()])
    if len(parts) < 2:
        return False

    # Check first part is yy-nnnn
    m_first = re.fullmatch(r'(\d{2})-(\d{1,6})', parts[0])
    if not m_first:
        return False
    nnnn_len = len(m_first.group(2))

    # Check all subsequent parts are either nnnn or yy-nnnn, and nnnn part matches length
    for part in parts[1:]:
        m_yy_nnnn = re.fullmatch(r'(\d{2})-(\d{1,6})', part)
        m_nnnn = re.fullmatch(r'\d{1,6}', part)
        if m_yy_nnnn:
            if len(m_yy_nnnn.group(2)) != nnnn_len:
                return False
        elif m_nnnn:
            if len(m_nnnn.group(0)) != nnnn_len:
                return False
        else:
            return False
    return True


def is_valid_simple_num_range(s):
    """
    Returns True if all ranges in s are of equal length and at least 3 digits long.
    Returns False if the string does not contain any digits.
    """
    if not re.search(r'\d', s):
        return False
    # if the range is mal-formatted like '3768-3769-3770-3782-3783' where there are more than one range back to back separated by dash, return False
    if re.search(r'(\d{3,6})-(\d{3,6})-(\d{3,6})', s):
        return False

    # Match all ranges like '123-125', '123 to 125', etc.
    for m in re.finditer(r'(\d{3,6})\s*(?:to|through|thru|-)\s*(\d{3,6})', s):
        start, end = m.group(1), m.group(2)
        if (
            len(start) != len(end) or
            start > end or
            (start[0] != end[0] and start[0] != str(int(end[0]) - 1))
        ):
            return False
    # Also ensure all numbers are at least 3 digits
    for num in re.findall(r'\d+', s):
        if len(num) < 3:
            return False
    return True


def is_valid_simple_format_range(s):
    """
    Returns True if all ranges in s are of yy-nnnn format and at least one valid range is present.
    Returns False if only single yy-nnnn numbers or a list separated by commas.
    """
    found_range = False
    # Match all ranges like '75-1471 — 75-1474', '90-1449 to 90-1451', '90-1449-90-1451', etc.
    for m in re.finditer(r'(\d{2})-(\d{1,6})\s*(?:to|through|thru|—|-)\s*(\d{2})-(\d{1,6})', s):
        start_yy, start_nnnn, end_yy, end_nnnn = m.group(1), m.group(2), m.group(3), m.group(4)
        if start_yy == end_yy and len(start_nnnn) == len(end_nnnn) and start_nnnn <= end_nnnn:
            found_range = True
        else:
            return False
    return found_range


def is_valid_multiples_format(s):
    """
    Returns True if the string contains multiple docket numbers of either nnnn or yy-nnnn format
    separated by comma or spaces, but NOT if nnnn is followed by yy-nnnn.
    """
    parts = [p.strip() for p in re.split(r'[\s,]+', s) if p.strip()]
    if len(parts) < 2:
        return False

    # Check that all parts are either nnnn or yy-nnnn
    formats = []
    for part in parts:
        if re.fullmatch(r'\d{1,6}', part):
            formats.append('nnnn')
        elif re.fullmatch(r'\d{2}-\d{1,6}', part):
            formats.append('yy-nnnn')
        else:
            return False

    # Return False if nnnn is immediately followed by yy-nnnn
    for i in range(len(formats) - 1):
        if formats[i] == 'yy-nnnn' and formats[i + 1] == 'nnnn':
            return False

    return True


def classify_docket_numbers(s):
    # If "d.c. civil action" or "civil action" or "icc ex parte appear" in the string, return "other"
    if ("d.c. civil action" in s or "civil action" in s or "criminal action" in s or "d.c." in s or "icc ex parte appear" in s):
        return 'other'

    # Containing only a single docket number of either nnnn or yy-nnnn format
    if re.fullmatch(r'\d{1,6}-?', s) or re.fullmatch(r'\d{1,2}-\d{1,6}-?', s):
        return 'singles'

    # Containing one or more docket numbers where the comma is used as a thousand separator
    # Matches patterns like '1,850', '11,066', '1,031, 1,032', '1,350,1,351'
    # But NOT '123,456' (three digits followed by three digits)
    elif re.fullmatch(r'(\d{1,2},\d{3})([\s,]+(\d{1,2},\d{3}))*', s):
        return 'thousands'

    # Containing docket numbers separated by slashes, where the part immediately before the first slash is in yy-nnnn format and all subsequent parts are either nnnn or yy-nnnn, with or without spaces around the slashes
    elif '/' in s and is_valid_slash_format(s):
        return 'slashes'

    # Containing docket numbers separated by commas, where the first part is in yy-nnnn format and all subsequent parts are nnnn, with or without spaces around the commas
    elif is_valid_delimiteds_format(s):
        return 'delimiteds'

    # Containing docket numbers of a range with "to" or "through" or "-" as the range indicator where the part before and the part after the indicator are equal digit numbers of format nnnn
    elif ("-" in s or "to" in s or "through" in s or "thru" in s) and "except" not in s and is_valid_simple_num_range(s):
        return 'simple_num_ranges'

    # Containing docket numbers of a range with "to" or "through" or "-" as the range indicator where the part before and the part after the indicator are dockets of format yy-nnnn with equal yy
    elif ("-" in s or "to" in s or "through" in s or "thru" in s) and "except" not in s and is_valid_simple_format_range(s):
        return 'simple_format_ranges'

    # Containing multiple docket numbers of either nnnn or yy-nnnn format separated by comma or spaces
    elif is_valid_multiples_format(s):
        return 'multiples'

    return 'other'


def remove_thousand_separators(s):
    """
    Removes thousand separators from numbers in a string.
    Thousand separators are commas between 1-3 digits and exactly 3 digits.
    Does not remove commas that act as delimiters.
    """
    # Pattern: 1-3 digits, comma, 3 digits, not followed by another digit
    # Handles cases like '1,850', '11,066', '1,031', '1,032', etc.
    pattern = r'(^|[^\d])(\d{1,3}),(\d{3})(?=\D|$)'
    while re.search(pattern, s):
        s = re.sub(pattern, r'\1\2\3', s)
    return s


def expand_slash_dockets(s):
    """
    Expands docket numbers separated by slashes.
    If the value before the slash is in yy-nnnn format, 
    the yy is prepended to the value after the slash.
    """
    # Find all groups separated by slashes
    parts = []
    for chunk in re.split(r',', s):
        parts.extend([p.strip() for p in re.split(r'/', chunk) if p.strip()])
    
    expanded = []
    last_yy = None

    for part in parts:
        # Match yy-nnnn format
        m = re.match(r'(\d{2})-(\d{1,6})$', part)
        if m:
            last_yy = m.group(1)
            expanded.append(f"{last_yy}-{m.group(2)}")
        else:
            # If part is just nnnn and we have a last_yy, prepend it
            m2 = re.match(r'(\d{1,6})$', part)
            if m2 and last_yy is not None:
                expanded.append(f"{last_yy}-{m2.group(1)}")
            else:
                # If part is full yy-nnnn or other, just add as is
                expanded.append(part)
    return expanded


def expand_delimited_dockets(s):
    """
    Handles cases where the first part is yy-nnnn and subsequent parts are nnnn.
    Prepends the yy to all subsequent nnnn parts.
    Example:
        '17-1222; 1250' -> ['17-1222', '17-1250']
        '91-6309, 6310' -> ['91-6309', '91-6310']
        '81-1087, 1088 and 1089' -> ['81-1087', '81-1088', '81-1089']
    """
    # Split on delimiters
    parts = [p.strip() for p in re.split(r',', s) if p.strip()]

    expanded = []
    yy = None

    for i, part in enumerate(parts):
        # Find yy-nnnn in the first part
        m = re.match(r'(\d{2})-(\d{1,6})$', part)
        if i == 0 and m:
            yy = m.group(1)
            expanded.append(f"{yy}-{m.group(2).zfill(4)}")
        elif yy is not None and re.match(r'^\d{1,6}$', part):
            expanded.append(f"{yy}-{part.zfill(4)}")
        elif re.match(r'^\d{2}-\d{1,6}$', part):
            expanded.append(part)
        else:
            # Ignore non-docket parts
            continue
    return expanded


def extract_docket_numbers(s):
    # Extract all valid docket numbers
    candidates = re.findall(r'\d{1,2}-\d{1,6}|\d{1,6}', s)
    cleaned = []
    for c in candidates:
        # Remove leading zeros in nnnn part, but keep for yy
        if '-' in c:
            yy, nnnn = c.split('-')
            cleaned.append(f"{yy.zfill(2)}-{nnnn.zfill(4)}")
        else:
            cleaned.append(str(int(c)))
    
    # Remove duplicates and sort
    cleaned = sorted(set(cleaned), key=lambda x: (len(x), x))
    return cleaned


def expand_simple_num_range(s):
    """
    Expands simple ranges in the format 'n to m' or 'n through m' or 'n-m'.
    Ensure the first letter of n and m are the same.
    """
    ranges = []
    parts = re.split(r',', s)
    for part in parts:
        part = part.strip()
        m = re.match(r'(\d{1,6})\s*(?:to|through|thru|-)\s*(\d{1,6})', part)
        if m and len(m.group(1)) == len(m.group(2)) and (m.group(1)[0] == m.group(2)[0] or m.group(1)[0] == str(int(m.group(2)[0]) - 1)):
            start = int(m.group(1))
            end = int(m.group(2))
            ranges.extend([str(i) for i in range(start, end + 1)])
        else:
            # If not a range, just add the part as is
            ranges.append(part)
    return ranges


def expand_simple_format_range(s):
    """
    Expands simple ranges in the format 'yy-n to yy-m' or 'yy-n through yy-m' or 'yy-n - yy-m'.
    """
    ranges = []
    parts = re.split(r',', s)
    for part in parts:
        part = part.strip()
        m = re.match(r'(\d{2})-(\d{1,6})\s*(?:to|through|thru|-)\s*(\d{2})-(\d{1,6})', part)
        if m:
            start_yy = m.group(1)
            start_nnnn = int(m.group(2))
            end_yy = m.group(3)
            end_nnnn = int(m.group(4))
            if start_yy == end_yy:
                ranges.extend([f"{start_yy}-{str(i).zfill(4)}" for i in range(start_nnnn, end_nnnn + 1)])
        else:
            # If not a range, just add the part as is
            ranges.append(part)
    return ranges


def handle_other_formats(s):
    return [s]


def clean_docket_numbers(s):
    s = prelim_clean(s)
    
    s_type = classify_docket_numbers(s)

    if s_type == 'thousands':
        s = remove_thousand_separators(s)
        
    elif s_type == 'slashes':
        s = ' '.join(expand_slash_dockets(s))

    elif s_type == 'delimiteds':
        s = ' '.join(expand_delimited_dockets(s))

    elif s_type == 'simple_num_ranges':
        s = ' '.join(expand_simple_num_range(s))

    elif s_type == 'simple_format_ranges':
        s = ' '.join(expand_simple_format_range(s))

    cleaned = extract_docket_numbers(s)

    if s_type == "other":
        cleaned = handle_other_formats(s)
    
    return s_type, cleaned
    

def confirm_docket_format(docket):
    """
    Confirms if a docket string is in nnnn (up to 6 digits) or yy-nnnn format.
    """
    if re.fullmatch(r'\d{1,6}', docket):
        return True
    if re.fullmatch(r'\d{2}-\d{1,6}', docket):
        yy, nnnn = docket.split('-')
        return 0 <= int(yy) <= 99 and 1 <= int(nnnn) <= 999999
    return False


def clean_docket_numbers_test(s, check):
    s = prelim_clean(s)
    
    s_type = classify_docket_numbers(s)

    if s_type != check:
        print(s_type)
        print(s)

    if s_type == 'thousands':
        s = remove_thousand_separators(s)
        
    
    elif s_type == 'slashes':
        s = ' '.join(expand_slash_dockets(s))


    elif s_type == 'delimiteds':
        s = ' '.join(expand_delimited_dockets(s))


    elif s_type == 'simple_num_ranges':
        s = ' '.join(expand_simple_num_range(s))

    elif s_type == 'simple_format_ranges':
        s = ' '.join(expand_simple_format_range(s))

    cleaned = extract_docket_numbers(s)

    if s_type == "other":
        cleaned = handle_other_formats(s)
    
    return cleaned



def test_clean_docket_numbers(test_cases, check):
    for raw, expected in test_cases.items():
        result = clean_docket_numbers_test(raw, check)
        assert result == expected, f"Failed for '{raw}': got {result}, expected {expected}"
        for r in result:
            assert confirm_docket_format(r), f"Format failed for '{r}' from '{raw}'"
    print(f"All tests passed for -- {check}.")



SINGLES = {
    '1833': ['1833'],
    '05-9': ['05-0009'],         
    '18-24': ['18-0024'],       
    '19-1586': ['19-1586'],
    '73--2205': ['73-2205'], 
    '13 — 4433': ['13-4433'],
    '14855_1': ['14855'],
    '87-1982_1': ['87-1982'],
    'No 15-5921': ['15-5921'],
    'No. 4814': ['4814'],
    'Ño. 16-5437': ['16-5437'],
    'No. 68-584': ['68-0584'],
    'Case 15-1836': ['15-1836'],
    'CASE NO. 16-3395': ['16-3395'],
    'Case No. 16-2190': ['16-2190'],
    'Case Nos. 15-3751, et al.': ['15-3751'],
    'Misc. 79-8089': ['79-8089'],
    'Misc. 229': ['229'],
    '87-1400 Orig.': ['87-1400'],
    'Docket 03-1864': ['03-1864'],
    'Docket 05-2780-CR': ['05-2780'],
    'Docket 04-2000-PR': ['04-2000'],
    'Docket 08-1886-cr': ['08-1886'],
    'Docket 10-3005-cv': ['10-3005'],
    'Docket 05-5414-ag': ['05-5414'],
    'Docket 06-0784-ev': ['06-0784'],
    'Docket 09-3690-pr': ['09-3690'],
    'Docket 08-4917-CV': ['08-4917'],
    'Docket 04-0917-AG': ['04-0917'],
    'Docket 05-1141-BK': ['05-1141'],
    'Docket 05-2590 PR': ['05-2590'],
    'Docket 05-0458 CR': ['05-0458'],
    'Docket 04-2223CR': ['04-2223'],
    '12-216 NAC': ['12-0216'],
    '09-4952-CV': ['09-4952'],
    '08-1903-ag': ['08-1903'],
    '11-1780-cv': ['11-1780'],
    '11-4820-cr': ['11-4820'],
    '10-908-pr': ['10-0908'],
    '12-4254-bk': ['12-4254'],
    '09-4822-ag NAC': ['09-4822'],
    '11-3264 NAC': ['11-3264'],
    '11-5323 (L)': ['11-5323'],
    '12-2322-bk (L)': ['12-2322'],
    '12-3493-cr (L)': ['12-3493'],
    '11-3294-cv(L), et al.': ['11-3294'],
    '07-2869-cr(XAP)': ['07-2869'],
    '07-4865-bk (XAP)': ['07-4865'],
    '94-7037L': ['94-7037'],
    '94-7129XAP': ['94-7129'],
    '04-6288 CR(CON)': ['04-6288'],
    '07-2279-ag (Con)': ['07-2279'],
    '10-3359-ag (Lead)': ['10-3359'],
    '10-1331-cr(Lead)': ['10-1331'],
    '04-2960-': ['04-2960'],
    '7-3766': ['07-3766'],
    '375, Bocket 25047': ['25047'],
    '104, Docket 25801': ['25801'],
    '857, Docket 75-2029': ['75-2029'],
    '1197, Docket 91-9325(L)': ['91-9325'],
    'Cal. 1360, Docket 84-1088': ['84-1088'],
    '12-71891; BRB 11-0533': ['12-71891'],
    '02-70048, INS A92-440-540': ['02-70048'],
    '94-70183; Tax Court 16266-91': ['94-70183'],
    '12-70259; Tax Ct. 20894-05': ['12-70259'],
    '13-16020; Adv. Pro. 10-90146': ['13-16020'],
    '80-5569; Bankruptcy 79-03326-M': ['80-5569'],
    '06-1148; Opposition 91124847': ['06-1148'],
    '05-11019; D.C. Docket 04-00102-CV-2': ['05-11019'],
    '05-11125; BIA A96-442-100': ['05-11125'],
    '03-16340; NLRB 12-CA-23237': ['03-16340']
}

MULTIPLES = {
    '80, 184': ['80', '184'],
    '5733, 5734': ['5733', '5734'],
    '96-2216, 97-1442, 96-2217': ['96-2216', '96-2217', '97-1442'],
    '96-1173, 96-1221 and 96-2231': ['96-1173', '96-1221', '96-2231'],
    '13652 and 13690': ['13652', '13690'],
    '77-1374 and 77-1375': ['77-1374', '77-1375'],
    '15-5850 & 15-5851': ['15-5850', '15-5851'],
    '77-3365, 77-3366, 77-3490, 77-3491 & 77-3553': ['77-3365', '77-3366', '77-3490', '77-3491', '77-3553'],
    '07-2141; 07-2555; 08-1403; 08-1545; 08-1763': ['07-2141', '07-2555', '08-1403', '08-1545', '08-1763'],
    'Nos. 87-3865 87-3902': ['87-3865', '87-3902'],
    'Nos. 946, 947': ['946', '947'],
    'Nos. 151 and 152': ['151', '152'],
    'Nos. 17518, 17519 and 17520': ['17518', '17519', '17520'],
    'Nos. 13-1840, 13-1896; 13-1849, 13-1897': ['13-1840', '13-1849', '13-1896', '13-1897'],
    'Nos. 92-3415, 92-3528 and 92-3529': ['92-3415', '92-3528', '92-3529'],
    'Nos. 05-6654-cr(L), 06-1202-cr, 06-1239-cr, 06-1860-cr, 06-4256-cr, 06-4257-cr': ['05-6654', '06-1202', '06-1239', '06-1860', '06-4256', '06-4257'],
    'Nos. 79- 3640, 79-3642': ['79-3640', '79-3642'],
    'Nos. 79-1795, 79-1800, and 79-1802 (Trial Group O)': ['79-1795', '79-1800', '79-1802'],
    'Case 14-3575, 14-3833, 14-3834, 15-3833': ['14-3575', '14-3833', '14-3834', '15-3833'],
    '13-1103 (L), 13-1402(con)': ['13-1103', '13-1402'],
    '11-2584-cv, 11-3808-cv': ['11-2584', '11-3808'],
    '12-2788-cr & 12-2789-cr': ['12-2788', '12-2789'],
    '13-459(L) 13-689(C)': ['13-0459', '13-0689'],
    '10-2680 (L) 10-4150 (L)': ['10-2680', '10-4150'],
    '13-200-cv 13-204-cv': ['13-0200', '13-0204'],
    '08-0524-cr(L), 08-2342-cr(CON)': ['08-0524', '08-2342'],
    '08-3201-ag (L), 09-0784-ag (Con)': ['08-3201', '09-0784'],
    '10-483-cv (L), 10-652-cv (XAP)': ['10-0483', '10-0652'],
    '12-4558-pr(L), 13-0131-pr(CON)': ['12-4558', '13-0131'],
    '10-2080-cr (Con), 10-2127-cr (Con), 10-2590-cr (Con)': ['10-2080', '10-2127', '10-2590'],
    '11-2272-bk (L); 11-2716-bk (Con)': ['11-2272', '11-2716'],
    '10-3359-ag (Lead), 10-3615-ag (XAP)': ['10-3359', '10-3615'],
    '10-1331-cr(Lead), 10-3893-cr(Con)': ['10-1331', '10-3893'],
    '11-2390-CR L, 11-4456, 11-5204': ['11-2390', '11-4456', '11-5204'],
    'Docket 01-2328, 01-2355': ['01-2328', '01-2355'],
    'Docket 05-2141-cv, 05-2326-cv': ['05-2141', '05-2326'],
    'Docket 04-5136 CR(L), 04-6288 CR(CON)': ['04-5136', '04-6288'],
    'Docket 07-2278-ag (L); 07-2279-ag (Con)': ['07-2278', '07-2279'],
    'Docket 07-4772-bk (L); 07-4843-bk (CON); 07-4845-bk (CON); 07-4865-bk (XAP);': ['07-4772', '07-4843', '07-4845', '07-4865'],
    'Docket 05-5523-cr(L), 06-0080-cr(con), 06-2392-cr(con)': ['05-5523', '06-0080', '06-2392'],
    'Docket 06-4567-cr, 06-4821-cr, 07-0025-cr, 07-2664-cr(L), 07-2869-cr(XAP)': ['06-4567', '06-4821', '07-0025', '07-2664', '07-2869'],
    'Docket 10-4519-cv(L), 10-4524-cv(CON)': ['10-4519', '10-4524'],
    'Docket 03-4744-AG(L), 04-1890-AG(CON)': ['03-4744', '04-1890'],
    'Docket 05-5485-ag, 05-6367-ag, 06-0004-ag, 06-2998-ag': ['05-5485', '05-6367', '06-0004', '06-2998'],
    'Docket 00-1386(L), 00-1407(CON) and 00-1425(CON)': ['00-1386', '00-1407', '00-1425'],
    'Docket Nos. 10-2258-cv(L), 10-2267-cv (con)': ['10-2258', '10-2267'],
    'Docket 12-3575(L), 12-3586(0': ['12-3575', '12-3586'],
    '1775, 1965, Dockets 94-7037L, 94-7129XAP': ['94-7037', '94-7129'],
    '474, 475, Dockets 71-1332, 71-1380': ['71-1332', '71-1380'],
    '545, 541, 532, 550, 551, 690, 689, Dockets 92-164(L), 92-1657, 92-1658, 92-1659, 92-1660, 92-1661, 92-1662, 92-1700': ['92-0164', '92-1657', '92-1658', '92-1659', '92-1660', '92-1661', '92-1662', '92-1700'],
    'Cal. 57, 84, Dockets 84-7253, 84-7255': ['84-7253', '84-7255'],
    '922 to 925, Dockets 83-1313, 83-1315, 83-1317 and 83-1318': ['83-1313', '83-1315', '83-1317', '83-1318'],
    '284, 285, 321; Dockets 25524, 25525, 25577': ['25524', '25525', '25577'],
    '05-16173, 05-16389, 05-16406, 05-16554. Adv. No. 98-2313-RCJ': ['05-16173', '05-16389', '05-16406', '05-16554'],
    '05-11791 and 05-11792; BIA Agency A95-551-857 & A95-551-858': ['05-11791', '05-11792'],
}

THOUSANDS = {
    '11,066': ['11066'],
    'No. 1,850': ['1850'],
    'NO. 2,240': ['2240'],
    'Nos. 1,031, 1,032, 1,034, 1,036': ['1031', '1032', '1034', '1036'],
    'Nos. 1,880 and 1,934': ['1880', '1934'],
    'Nos. 1,350,1,351': ['1350', '1351'],
}

SLASHES = {
    '85-1760/1799': ['85-1760', '85-1799'],
    '89-1150/1171, 89-1208/1209': ['89-1150', '89-1171', '89-1208', '89-1209'],
    '08-2068/2069/2079/2082': ['08-2068', '08-2069', '08-2079', '08-2082'],
    '13-4145/ 14-3816/ 15-3462': ['13-4145', '14-3816', '15-3462'],
    '15-2037/15-2197': ['15-2037', '15-2197'],
    '16-2262 / 16-2306': ['16-2262', '16-2306'],
    '15-6014/6402/16-5356': ['15-6014', '15-6402', '16-5356'],
    '17-2020/18-1106; 18-1137': ['17-2020', '18-1106', '18-1137'],
    'No. 00-3762/3961': ['00-3762', '00-3961'],
    'Case 15-2584/16-1064': ['15-2584', '16-1064'],
    'Nos. 15-2305/2478': ['15-2305', '15-2478'],
    'Nos. 14-6081/14-6451/15-5045/15-5738': ['14-6081', '14-6451', '15-5045', '15-5738'],
    'Nos. 14-4083/ 4084/ 4132/ 4133/ 15-3295/ 3296/ 3380/ 3381': ['14-4083', '14-4084', '14-4132', '14-4133', '15-3295', '15-3296', '15-3380', '15-3381'],
    '05-11847/11848; Agency A95-886-023 & A95-886-024': ['05-11847', '05-11848']
}

DELIMITEDS = {
    '17-1222; 1250': ['17-1222', '17-1250'],
    '91-6309, 6310': ['91-6309', '91-6310'],
    '81-1087, 1088 and 1089': ['81-1087', '81-1088', '81-1089'],
    '258, 259 and 260, Dockets 80-7436, 7438 and 7446': ['80-7436', '80-7438', '80-7446'],
    '04-20917, 20918; USDC 4:03-CR-331-6, 4:03-CR-330-7': ['04-20917', '04-20918']
}

SIMPLE_NUM_RANGES = {
    'Nos. 154-158': ['154', '155', '156', '157', '158'],
    'Nos. 4076-4079': ['4076', '4077', '4078', '4079'],
    'Nos. 4734-4740, 4770-4773': ['4734', '4735', '4736', '4737', '4738', '4739', '4740', '4770', '4771', '4772', '4773'],
    'Nos. 167-170 and 182-190': ['167', '168', '169', '170', '182', '183', '184', '185', '186', '187', '188', '189', '190'],
    'Nos. 5399, 5400, 5412-5414': ['5399', '5400', '5412', '5413', '5414'],
    '167 to 170 and 182-190': ['167', '168', '169', '170', '182', '183', '184', '185', '186', '187', '188', '189', '190'],
    '5399, 5400, 5412 through 5414': ['5399', '5400', '5412', '5413', '5414'],
    '12769-12770': ['12769', '12770'],
    '5296-5298_1': ['5296', '5297', '5298'],
    '12463-12468_1': ['12463', '12464', '12465', '12466', '12467', '12468'],
    '8802-8808, 8614, 8894': ['8614', '8802', '8803', '8804', '8805', '8806', '8807', '8808', '8894'],
    '219-220, Dockets 31638-31639': ['31638', '31639'],
    'No. 4819, with Nos. 4820-4827': ['4819', '4820', '4821', '4822', '4823', '4824', '4825', '4826', '4827']
}

SIMPLE_FORMAT_RANGES = {
    '75-1312 to 75-1314': ['75-1312', '75-1313', '75-1314'],
    '84-3743 to 84-3753, 84-3842 and 84-3868': ['84-3743', '84-3744', '84-3745', '84-3746', '84-3747', '84-3748', '84-3749', '84-3750', '84-3751', '84-3752', '84-3753', '84-3842', '84-3868'],
    '82-8413, 82-8418, 82-8426 to 82-8432 and 82-8435': ['82-8413', '82-8418', '82-8426', '82-8427', '82-8428', '82-8429', '82-8430', '82-8431', '82-8432', '82-8435'],
    '84-3743 through 84-3753, 84-3842 and 84-3868': ['84-3743', '84-3744', '84-3745', '84-3746', '84-3747', '84-3748', '84-3749', '84-3750', '84-3751', '84-3752', '84-3753', '84-3842', '84-3868'],
    '75-1471 — 75-1474': ['75-1471', '75-1472', '75-1473', '75-1474'],
    '17-3550 thru 17-3555; August Term, 2018': ['17-3550', '17-3551', '17-3552', '17-3553', '17-3554', '17-3555'],
    '80-5041 to 80-5044, and 80-5072': ['80-5041', '80-5042', '80-5043', '80-5044', '80-5072'],
    '84-1838 to 84-1840, 84-1842, 84-1844, 84-1845, 84-1881 and 84-1883 to 84-1885': ['84-1838', '84-1839', '84-1840', '84-1842', '84-1844', '84-1845', '84-1881', '84-1883', '84-1884', '84-1885'],
    '90-1449 to 90-1451 and 90-1500': ['90-1449', '90-1450', '90-1451', '90-1500'],
    '78-3299-78-3301': ['78-3299', '78-3300', '78-3301'],
    '98-2416-98-2418, and 98-2420': ['98-2416', '98-2417', '98-2418', '98-2420'],
    '79-5222-79-5224, 79-5269 and 79-5270': ['79-5222', '79-5223', '79-5224', '79-5269', '79-5270'],
    'Nos. 16-5149 through 16-5158': ['16-5149', '16-5150', '16-5151', '16-5152', '16-5153', '16-5154', '16-5155', '16-5156', '16-5157', '16-5158'],
    'Nos. 77-1463 to 77-1465': ['77-1463', '77-1464', '77-1465'],
    'Nos. 92-5509, 92-5512, 92-5521 to 92-5523, 92-5529 and 92-5730': ['92-5509', '92-5512', '92-5521', '92-5522', '92-5523', '92-5529', '92-5730'],
    'Nos. 83-3874 to 83-3876 and 83-3893 to 83-3895 and 84-3530 to 84-3532': ['83-3874', '83-3875', '83-3876', '83-3893', '83-3894', '83-3895', '84-3530', '84-3531', '84-3532'],
    'Nos. 76-2525-76-2527, 76-2158': ['76-2158', '76-2525', '76-2526', '76-2527'],
    'Nos. 71-2018-71-2021': ['71-2018', '71-2019', '71-2020', '71-2021'],
    'Nos. 73-2191-73-2192 and 73-2193': ['73-2191', '73-2192', '73-2193'],
    'Nos. 75-2446 to 75-2448, 76-1148-76-1150 and 76-1721 to 76-1724': ['75-2446', '75-2447', '75-2448', '76-1148', '76-1149', '76-1150', '76-1721', '76-1722', '76-1723', '76-1724'],
    'Docket 97-1041(L) thru 97-1061': ['97-1041', '97-1042', '97-1043', '97-1044', '97-1045', '97-1046', '97-1047', '97-1048', '97-1049', '97-1050', '97-1051', '97-1052', '97-1053', '97-1054', '97-1055', '97-1056', '97-1057', '97-1058', '97-1059', '97-1060', '97-1061'],
    '866 and 925 to 929, Dockets 80-1013 to 80-1018': ['80-1013', '80-1014', '80-1015', '80-1016', '80-1017', '80-1018']
}



# from clean_utils import *
def run_tests():
    test_clean_docket_numbers(test_cases=SINGLES, check="singles")
    test_clean_docket_numbers(test_cases=MULTIPLES, check="multiples")
    test_clean_docket_numbers(test_cases=THOUSANDS, check="thousands")
    test_clean_docket_numbers(test_cases=SLASHES, check="slashes")
    test_clean_docket_numbers(test_cases=DELIMITEDS, check="delimiteds")
    test_clean_docket_numbers(test_cases=SIMPLE_NUM_RANGES, check="simple_num_ranges")
    test_clean_docket_numbers(test_cases=SIMPLE_FORMAT_RANGES, check="simple_format_ranges")

