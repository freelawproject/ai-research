import re
import os

from gpt_clean_utils import extract_with_llm
from prompt import *

BATCH_SIZE = 10
MAX_RETRIES = 2

MINI_SYS_PROMPTS = {"fed_appellate": FED_APPELLATE}
FULL_SYS_PROMPTS = {"fed_appellate": FED_APPELLATE}
TIE_BREAKER_SYS_PROMPTS = {"fed_appellate": TIE_BREAKER_FED_APPELLATE}

COURT_MAP = {"scotus": "fed_appellate",
             "cadc": "fed_appellate",
             "ca1": "fed_appellate",
             "ca2": "fed_appellate",
             "ca3": "fed_appellate",
             "ca4": "fed_appellate",
             "ca5": "fed_appellate",
             "ca6": "fed_appellate",
             "ca7": "fed_appellate",
             "ca8": "fed_appellate",
             "ca9": "fed_appellate",
             "ca10": "fed_appellate",
             "ca11": "fed_appellate"}

GENERIC_PATTERNS = {"fed_appellate": [
        r'\d{1,2}-\d{1,6}-pr',
        r'\d{1,2}-\d{1,6}-op',
        r'\d{1,2}-\d{1,6}-cr',
        r'\d{1,2}-\d{1,6}-cv',
        r'\d{1,2}-\d{1,6}-bk',
        r'\d{1,2}-\d{1,6}-am',
        r'\d{1,2}-\d{1,6}-ag',
        r'\d{1,2}-\d{1,6}P',
        r'\d{1,2}-\d{1,6}U',
        r'\d{1,2}M\d{1,6}',
        r'\d{1,2}A\d{1,6}',
        r'D-\d{1,6}',
        r'A-\d{1,6}',
        r'\d{1,2}-\d{1,6}',
        r'\d{1,6}'
    ]}


# Existing helper function in cl/lib/string_utils.py with minor modifications
def normalize_dashes(text: str) -> str:
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


#########################
#########################

def is_generic(s, court_map):
    patterns = GENERIC_PATTERNS.get(court_map, [])
    if any(re.fullmatch(p, s) for p in patterns):
        return True
    return False


def prelim_clean_fed_appellate(s):
    # Normalize dashes
    s = normalize_dashes(s)

    # Remove space around dashes & trim leading or trailing spaces
    s = re.sub(r'\s*-\s*', '-', s)
    s = s.strip()

    # Remove trailing numbers followed by underscores
    s = re.sub(r'_\d+$', '', s) # eg. '17-1222_1', '1222_2'

    # Remove leading dash
    s = re.sub(r'^-+', '', s) # eg. '-17-1222', '-1222'

    # Remove trailing dash
    s = re.sub(r'-+$', '', s) # eg. '17-1222-', '1222-'

    # Remove trailing period
    s = re.sub(r'\.+$', '', s) # eg. '17-1222.', '1222...'

    # Remove leading "No.", "No", "Case No.", "Case No", "Docket No.", "Docket", "Case" prefixes.
    s = re.sub(r'^(No\.?|Case No\.?|Docket No\.?|Docket|Case)\s+', '', s, flags=re.IGNORECASE)

    return s


def regex_clean_fed_appellate(s):
    # All patterns to match
    patterns = GENERIC_PATTERNS.get("fed_appellate", [])
    # Combine patterns into one regex
    combined_pattern = '|'.join(patterns)
    candidates = re.findall(combined_pattern, s, flags=re.IGNORECASE)
    cleaned = []
    for c in candidates:
        # Handle patterns with dash and suffix (e.g., 12-1234-ag)
        m = re.match(r'(\d{1,2})-(\d{1,6})-([a-z]{2})', c, flags=re.IGNORECASE)
        if m:
            yy, nnnn, suffix = m.groups()
            cleaned.append(f"{yy.zfill(2)}-{nnnn}-{suffix}")
            continue
        # Handle patterns with dash and single-letter suffix (e.g., 12-1234P)
        m = re.match(r'(\d{1,2})-(\d{1,6})([a-z])$', c, flags=re.IGNORECASE)
        if m:
            yy, nnnn, suffix = m.groups()
            cleaned.append(f"{yy.zfill(2)}-{nnnn}{suffix}")
            continue
        # Handle patterns like 12-1234
        m = re.match(r'(\d{1,2})-(\d{1,6})$', c, flags=re.IGNORECASE)
        if m:
            yy, nnnn = m.groups()
            cleaned.append(f"{yy.zfill(2)}-{nnnn}")
            continue
        # Handle patterns like 12A1234 or 12M1234
        m = re.match(r'(\d{1,2})([am])(\d{1,6})', c, flags=re.IGNORECASE)
        if m:
            yy, letter, nnnn = m.groups()
            cleaned.append(f"{yy.zfill(2)}{letter}{nnnn}")
            continue
        # Handle patterns like A-1234 or D-1234
        m = re.match(r'([ad])-(\d{1,6})', c, flags=re.IGNORECASE)
        if m:
            letter, nnnn = m.groups()
            cleaned.append(f"{letter}-{nnnn}")
            continue
        # Handle just numbers
        m = re.match(r'^\d{1,6}$', c, flags=re.IGNORECASE)
        if m:
            cleaned.append(str(int(c)))
            continue
    
    cleaned = [s.upper() for s in cleaned]
    return "; ".join(cleaned)


# Real-time processing of individual records
def clean_docket_number(docket_id, docket_number_raw, court_id, llm_batch):
    court_map = COURT_MAP.get(court_id, None)
    if court_map:
        prelim_func = globals().get(f"prelim_clean_{court_map}", None)
        if prelim_func:
            cleaned = prelim_func(docket_number_raw)
            if is_generic(cleaned, court_map):
                regex_func = globals().get(f"regex_clean_{court_map}", None)
                if regex_func:
                    docket_number = regex_func(cleaned)
            else:
                docket_number = docket_number_raw
                llm_batch.append(docket_id) # replace with redis cache in production
    else:
        docket_number = docket_number_raw
    
    return docket_number, llm_batch # replace with DB save in production


# LLM batch processing helper function with recursion for retries
def process_llm_batches(llm_batches, 
                        system_prompt,
                        model_id,
                        retry=0, 
                        max_retries=MAX_RETRIES, 
                        batch_size=BATCH_SIZE,
                        all_cleaned=[]
                        ):
    print(f"---Processing {len(llm_batches)} records with {model_id}---")
    batches = [llm_batches[i:i + batch_size] for i in range(0, len(llm_batches), batch_size)]
    _, parsed_output = extract_with_llm(batches, system_prompt, model_id)
    all_cleaned.extend(parsed_output)
    # Check for any still-unprocessed records (e.g., if LLM failed to extract)
    processed_ids = [k for d in all_cleaned for k in d.keys()]
    remaining = [batch for batch in llm_batches if list(batch.keys())[0] not in processed_ids]
    print(f"---{len(remaining)} remaining---")
    if remaining and retry < max_retries:
        # Recurse on remaining
        print(f"---Retry {retry + 1}---")
        retry += 1
        return process_llm_batches(remaining, system_prompt=system_prompt, model_id=model_id, retry=retry, max_retries=max_retries, batch_size=int(batch_size/2), all_cleaned=all_cleaned)
    elif remaining and retry >= max_retries:
        print(f"---Max retries reached. {len(remaining)} records remain unprocessed.---")
        for batch in remaining:
            docket_id = list(batch.keys())[0]
            all_cleaned.append({docket_id: batch.get(docket_id, "")}) # Assign raw if still unprocessed
    return {k: v for d in all_cleaned for k, v in d.items()}


# LLM batch processing of multiple records
def llm_clean_docket_numbers(docket_db, llm_batch):
    court_batches = {}
    llm_cleaned = {}
    
    # Group batches by court type
    for docket_id in llm_batch:
        court_id, docket_number_raw = docket_db.get(docket_id, (None, None)) # replace with DB fetch in production
        court_map = COURT_MAP.get(court_id, None)
        if court_map not in court_batches:
            court_batches[court_map] = []
        court_batches[court_map].append({docket_id: docket_number_raw})

    # Process each court batch
    for court_map, batches in court_batches.items():
        print(f"Processing court: {court_map} with {len(batches)} records")

        if court_map:
            full_model_batches = []
            # First pass with two mini models to find consensus
            mini_model_one_results = process_llm_batches(batches, system_prompt=MINI_SYS_PROMPTS.get(court_map, ""), model_id="gpt-4o-mini", retry=0, max_retries=MAX_RETRIES, batch_size=BATCH_SIZE, all_cleaned=[])
            mini_model_two_results = process_llm_batches(batches, system_prompt=MINI_SYS_PROMPTS.get(court_map, ""), model_id="gpt-4.1-mini", retry=0, max_retries=MAX_RETRIES, batch_size=BATCH_SIZE, all_cleaned=[])
            # Compare results and create batch for prediction with larger model
            for docket_id, docket_number_one in mini_model_one_results.items():
                docket_number_two = mini_model_two_results.get(docket_id, None)
                if docket_number_one == docket_number_two and docket_number_one != '':
                    llm_cleaned[docket_id] = (docket_number_one, "mini")
                else:
                    full_model_batches.append({docket_id: docket_db.get(docket_id, (None, None))[1]})
            if full_model_batches:
                tie_breaker_batches = []
                # Second pass with two full models to find consensus
                full_model_one_results = process_llm_batches(full_model_batches, system_prompt=FULL_SYS_PROMPTS.get(court_map, ""), model_id="gpt-4o", retry=0, max_retries=MAX_RETRIES, batch_size=BATCH_SIZE, all_cleaned=[])
                full_model_two_results = process_llm_batches(full_model_batches, system_prompt=FULL_SYS_PROMPTS.get(court_map, ""), model_id="gpt-4.1", retry=0, max_retries=MAX_RETRIES, batch_size=BATCH_SIZE, all_cleaned=[])
                # Compare results and assign docket_number_raw as docket_number if no consensus
                for docket_id, docket_number_one in full_model_one_results.items():
                    docket_number_two = full_model_two_results.get(docket_id, None)
                    if docket_number_one == docket_number_two and docket_number_one != '':
                        llm_cleaned[docket_id] = (docket_number_one, "full")
                    else:
                        tie_breaker_batches.append({docket_id: {"docket_number": docket_db.get(docket_id, (None, None))[1],
                                                                "attempt_1": docket_number_one,
                                                                "attempt_2": docket_number_two}})
                if tie_breaker_batches:
                    # Third pass with tie-breaker model
                    tie_breaker_results = process_llm_batches(tie_breaker_batches, system_prompt=TIE_BREAKER_SYS_PROMPTS.get(court_map, ""), model_id="gpt-4o", retry=0, max_retries=MAX_RETRIES, batch_size=BATCH_SIZE, all_cleaned=[])
                    for docket_id, docket_number in tie_breaker_results.items():
                        llm_cleaned[docket_id] = (docket_number, "tie_breaker")
        else:
            llm_cleaned = {list(batch.keys())[0]: (list(batch.values())[0], "raw") for batch in batches} # Assign raw if no court_map
    return llm_cleaned


def do_cleaning(docket_df):
    llm_batch = []
    print(f"***Processing {len(docket_df)} records***")
    # Delete raw_output.jsonl and parsed_output.jsonl if they exist
    if os.path.exists("predictions/raw_output.jsonl"):
        os.remove("predictions/raw_output.jsonl")
    if os.path.exists("predictions/parsed_output.jsonl"):
        os.remove("predictions/parsed_output.jsonl")

    for i, row in docket_df.iterrows():
        docket_id = row['docket_id']
        docket_number_raw = row['docket_number_raw']
        court_id = row['court_id']
        docket_number, llm_batch = clean_docket_number(docket_id, docket_number_raw, court_id, llm_batch)
        docket_df.loc[docket_df['docket_id'] == docket_id, 'docket_number'] = docket_number # replace with DB save in production
        docket_df.loc[docket_df['docket_id'] == docket_id, 'cleaned_with'] = 'regex'

    if llm_batch:
        print(f"***Processing {len(llm_batch)} records with LLM***")
        docket_db = {row['docket_id']: (row['court_id'], row['docket_number_raw']) for _, row in docket_df.iterrows()} # replace with DB fetch in production
        llm_cleaned = llm_clean_docket_numbers(docket_db, llm_batch)
        for docket_id, (docket_number, model_used) in llm_cleaned.items():
            docket_df.loc[docket_df['docket_id'] == docket_id, 'docket_number'] = docket_number # replace with DB save in production
            docket_df.loc[docket_df['docket_id'] == docket_id, 'cleaned_with'] = model_used

    return docket_df
