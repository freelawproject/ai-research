import re
from pypdf import PdfReader

def extract_text_from_pdf(path):
    reader = PdfReader(path)
    return [page.extract_text() for page in reader.pages]


def clean_court(court_str):
    courts = court_str.split(";")
    cleaned = []
    for court in courts:
        court = court.strip()
        # Remove leading digits and optional punctuation (e.g., "1SC-Pa." → "SC-Pa.")
        court = re.sub(r"^\d+|^\d*\)*", "", court)
        cleaned.append(court.strip(" ."))
    return cleaned


# def parse_granted_list(pages):
#         all_data = []

#         # Regex for Orig. dockets (e.g. "141, Orig.  TEXAS V. NEW MEXICO")
#         docket_orig_re = re.compile(r"^(\d{1,4},\s*Orig\.)\s+(.*)")

#         # Regex for standard dockets (e.g. "24-820) CFY CASE NAME")
#         docket_re = re.compile(r"^(\d{2}(?:[-A-Za-z]{0,3})?\d{1,4})\)?\s*#?\s*\d*\s*([A-Z]{2,6})\s+(.+)")
#         court_line_re = re.compile(r"^\s*Court:\s+(.*?)(?:\s+(Grant|Granted|Noted):\s+([\d/]+)(?:\s+\(?.*?)?)?\s*$")
#         arg_decide_line_re = re.compile(r"^\s*Argument Date:\s+([\d/]+)\s+Decided:\s+([\d/]+)")
#         result_re = re.compile(r"Result:\s+(.*?)\s{2,}|Result:\s+(.+)$", re.IGNORECASE)

#         for page in pages:
#             lines = page.splitlines()
#             current = {}
#             for i, line in enumerate(lines):

#                 # Docket + Type + Case name
#                 orig_match = docket_orig_re.match(line)
#                 if orig_match:
#                     docket_num, case_name = orig_match.groups()
#                     current = {
#                         "docket_number": docket_num.strip(),
#                         "type": None,
#                         "case_name": case_name.strip(),
#                         "court_ids": [],
#                         "dates": {}
#                     }
#                     all_data.append(current)
#                     continue

#                 standard_match = docket_re.match(line)
#                 if standard_match:
#                     docket_num, docket_type, case_name = standard_match.groups()
#                     current = {
#                         "docket_number": docket_num.strip(),
#                         "type": docket_type.strip(),
#                         "case_name": case_name.strip(),
#                         "court_ids": [],
#                         "dates": {}
#                     }
#                     all_data.append(current)
#                     continue

#                 # Court + Date line
#                 court_match = court_line_re.match(line)
#                 if court_match and current:
#                     court_str, date_type, date_val = court_match.groups()
#                     current["court_ids"] = clean_court(court_str)
#                     if date_val:
#                         current["dates"][date_type.lower()] = date_val.strip()
#                     continue

#                 # Argument Date + Decided line
#                 arg_decide_match = arg_decide_line_re.match(line)
#                 if arg_decide_match and current:
#                     arg_date, decide_date = arg_decide_match.groups()
#                     current["dates"]["argument_date"] = arg_date.strip()
#                     current["dates"]["decided_date"] = decide_date.strip()
#                     continue
                
#                 # Result
#                 result_match = result_re.search(line)
#                 if result_match and current:
#                     current["result"] = (result_match.group(1) or result_match.group(2)).strip()
                    
#         return all_data

def parse_granted_list(pages):
    all_data = []

    # Regexes
    docket_orig_re = re.compile(r"(\d{1,4},\s*Orig\.)\s+(.*?)\s{2,}")  # Require 2+ spaces at end
    docket_re = re.compile(r"(\d{2}(?:[-A-Za-z]{0,3})?\d{1,4})\**#*\s*([A-Z]{2,6})\s+(.*?)\s{2,}")
    
    court_re = re.compile(r"Court:\s+(.*?)\s{2,}")
    granted_re = re.compile(r"Grant(?:ed)?\s*:\s*([\d/]+)")
    argument_re = re.compile(r"Argument Date:\s+([\d/]+)")
    decided_re = re.compile(r"Decided:\s+([\d/]+)")
    result_re = re.compile(r"(?:R\s*E\s*S\s*U\s*L\s*T)\s*:\s*(.*?)(?=(?:Author:|Other:|$))",re.IGNORECASE)

    for page in pages:
        blocks = re.split(r"(?:\n\s*){3,}", page)

        for block in blocks:
            flat = block.replace("\n", " ").strip()
            if not flat:
                continue

            current = None

            # Try matching docket
            orig_match = docket_orig_re.search(flat)
            if orig_match:
                docket_num, case_name = orig_match.groups()
                current = {
                    "docket_number": docket_num.strip(),
                    "type": None,
                    "case_name": case_name.strip(),
                    "court_ids": [],
                    "dates": {}
                }
                all_data.append(current)

            else:
                standard_match = docket_re.search(flat)
                if standard_match:
                    docket_num, docket_type, case_name = standard_match.groups()
                    current = {
                        "docket_number": docket_num.strip(),
                        "type": docket_type.strip(),
                        "case_name": case_name.strip(),
                        "court_ids": [],
                        "dates": {}
                    }
                    all_data.append(current)

            if current:
                court_match = court_re.search(flat)
                if court_match:
                    court_str = court_match.group(1)
                    current["court_ids"] = clean_court(court_str)

                granted_match = granted_re.search(flat)
                if granted_match:
                    current["dates"]["granted_date"] = granted_match.group(1).strip()

                arg_match = argument_re.search(flat)
                if arg_match:
                    current["dates"]["argument_date"] = arg_match.group(1).strip()

                decided_match = decided_re.search(flat)
                if decided_match:
                    current["dates"]["decided_date"] = decided_match.group(1).strip()

                result_match = result_re.search(flat)
                if result_match:
                    current["result"] = result_match.group(1).strip()

    return all_data


def fill_missing_court_and_dates(cases):
    n = len(cases)

    for i in range(n):
        current = cases[i]

        if not current.get("court_ids") and not current.get("dates"):
            for j in range(i + 1, n):
                court_ids = cases[j].get("court_ids")
                dates = cases[j].get("dates")
                if court_ids:
                    current["court_ids"] = court_ids
                if dates:
                    current["dates"] = dates
                    break
                   
    return cases


def parse_granted_list_pdf(pdf_path):
    pages_text = extract_text_from_pdf(pdf_path)
    parsed_text = parse_granted_list(pages_text)
    filled_data = fill_missing_court_and_dates(parsed_text)
    return filled_data