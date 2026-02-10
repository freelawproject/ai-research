import json
import re
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup


def normalize_date(date_str):
    """Convert date string to YYYY-MM-DD format."""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str.strip(), "%B %d, %Y").strftime("%Y-%m-%d")
    except ValueError:
        return None


def clean_lower_court_cases(raw_text):
    """Clean and normalize lower court case numbers from various formats."""
    raw_text = raw_text.replace("(", "").replace(")", "").strip()
    # Case 1: Slash-separated numbers with the same prefix: "98-35309/35509" or  "96-C-235/239/240/241"
    if re.match(r"^\d+-[A-Za-z]*-?\d+(/\d+)+$", raw_text):
        parts = raw_text.split("/")
        cleaned_parts = []

        # Extract the prefix from the first part
        prefix_match = re.match(r"(.*-)(\d+)$", parts[0])
        if prefix_match:
            current_prefix = prefix_match.group(1)
            first_number = prefix_match.group(2)
            cleaned_parts.append(f"{current_prefix}{first_number}")

            # Apply the prefix to all subsequent parts
            for part in parts[1:]:
                # Remove any leading/trailing whitespace
                part = part.strip()
                # If part is a number without prefix, add the current prefix
                if re.match(r"^\d+$", part):
                    cleaned_parts.append(f"{current_prefix}{part}")
                else:
                    # If part has its own prefix, use it as is
                    cleaned_parts.append(part)
            return ", ".join(cleaned_parts)
        else:
            # If prefix extraction fails, return the raw text
            return raw_text

    # Case 2: Various separators with inherited prefixes: "98-4033;-4214;-4246"
    if re.match(r"^\d+-\d+([;/,-]-\d+)+$", raw_text):
        parts = re.split(r"[;/,-]", raw_text)
        prefix = parts[0].split("-")[0]
        cleaned_parts = [
            f"{prefix}-{part.strip('-')}"
            for part in parts
            if part.strip("-") and part.strip("-") != prefix
        ]
        return ", ".join(cleaned_parts)

    # Case 3: Negative prefix-separated numbers: "99-1845,-1846,-1847,-197" or "98-60240,-60454,-60467,-""
    if re.match(r"^\d+-\d+(,-\d+)*,-?$", raw_text):
        prefix_match = re.match(r"(\d+-)(\d+)", raw_text)
        if prefix_match:
            current_prefix = prefix_match.group(1)
            remaining_text = raw_text[len(prefix_match.group(0)) :]
            numbers = [prefix_match.group(2)] + remaining_text.split(",")
            # Exclude empty strings resulting from trailing hyphens
            cleaned_parts = [
                f"{current_prefix}{num.strip('-')}" for num in numbers if num.strip("-")
            ]
            return ", ".join(cleaned_parts)
        else:
            return raw_text

    # Case 4: Comma-separated items with prefixes already correct: "33094-CW, 33095-CW"
    if re.match(r"^(\d+-[A-Za-z]+, )*\d+-[A-Za-z]+$", raw_text):
        return raw_text

    # Case 5: Mixed slash-separated values with a shared prefix: "98-16950/17044/17137"
    if re.match(r"^\d+-\d+(/\d+)+$", raw_text):
        parts = raw_text.split("/")
        prefix = parts[0].split("-")[0]
        cleaned_parts = [f"{prefix}-{part.split('-')[-1]}" for part in parts]
        return ", ".join(cleaned_parts)

    # Case 6: Ampersand-separated with prefixes or 'See also'-separated with prefixes: "95-56639 & 96-55194" or "95-56639 See also 96-55194"
    if "&" in raw_text:
        return raw_text.replace(" & ", ", ")

    if "See also" in raw_text:
        return raw_text.replace(" See also ", ", ")
    
    # Case 7: Preserve specific formats: "CR-99-1140", "1998-CA-0022039-MR"
    if re.match(r"^(CR|CA)-\d+-\d+$", raw_text) or re.match(
        r"^\d+-\d+-\d+-[A-Za-z]+$", raw_text
    ):
        return raw_text

    # Case 8: Range expansion: "97-1715/98-1111 to 1115" or "97-1715/1111 to 1115"
    if re.search(r"\d+/\d+(-\d+)? to \d+", raw_text):
        parts = raw_text.split("/")
        cleaned_parts = [
            parts[0]
        ]  # Start with the first part, which has the full prefix

        # Process the range part
        range_part = parts[1]

        # Check if the range start has a different prefix than the main prefix
        if " to " in range_part:
            start_part, end_part = range_part.split(" to ")
            if "-" in start_part:  # Different prefix specified in the range
                start_prefix, start_number = re.match(
                    r"(\d+)-(\d+)", start_part
                ).groups()
                if "-" in end_part:  # Full end prefix given
                    end_prefix, end_number = re.match(r"(\d+)-(\d+)", end_part).groups()
                    # Expand with the specified prefixes
                    cleaned_parts.append(f"{start_prefix}-{start_number}")
                    if start_prefix == end_prefix:
                        cleaned_parts.extend(
                            [
                                f"{start_prefix}-{i}"
                                for i in range(
                                    int(start_number) + 1, int(end_number) + 1
                                )
                            ]
                        )
                    else:
                        cleaned_parts.extend(
                            [
                                f"{end_prefix}-{i}"
                                for i in range(
                                    int(start_number) + 1, int(end_number) + 1
                                )
                            ]
                        )
                else:  # Inherit prefix only for the end of the range
                    end_number = int(end_part)
                    cleaned_parts.append(f"{start_prefix}-{start_number}")
                    cleaned_parts.extend(
                        [
                            f"{start_prefix}-{i}"
                            for i in range(int(start_number) + 1, end_number + 1)
                        ]
                    )
            else:
                # If no new prefix in range start, inherit prefix from the first part
                prefix = parts[0].split("-")[0]
                start, end = map(int, range_part.split(" to "))
                cleaned_parts.extend([f"{prefix}-{i}" for i in range(start, end + 1)])
        else:
            # No range detected, handle as simple case
            cleaned_parts.append(f"{parts[0].split('-')[0]}-{range_part.strip()}")

        return ", ".join(cleaned_parts)

    # Case 9: Various separators with inherited prefixes: "98-4033;-4214;-4246"
    if re.match(r"^\d+-\d+([;/,-]-\d+)+$", raw_text):
        parts = re.split(r"[;/,-]", raw_text)
        prefix = parts[0].split("-")[0]
        cleaned_parts = [f"{prefix}-{part.strip('-')}" for part in parts]
        return ", ".join(cleaned_parts)

    # Case 10: Negative prefix-separated numbers: "99-1845,-1846,-1847,-197"
    if re.match(r"^\d+-\d+(,-\d+)+$", raw_text):
        prefix = raw_text.split("-")[0]
        numbers = re.split(r",", raw_text)
        cleaned_parts = [f"{prefix}-{num.strip('-')}" for num in numbers]
        return ", ".join(cleaned_parts)

    # General replacement for remaining semicolons, new line, or ampersands
    text = re.sub(r"[;&\n]", ",", raw_text)

    # Clean up any extra spaces and return the result
    return ", ".join(part.strip() for part in text.split(","))


def format_scotus_metadata(
    docket_number=None,
    docket_date=None,
    case_title=None,
    lower_court=None,
    lower_court_case_numbers_raw=None,
    lower_court_decision_date=None,
    lower_court_rehearing_denied_date=None,
):
    return {
        "case_number": docket_number.replace(" *** CAPITAL CASE ***", "").strip() if docket_number else None,
        "docket_date": normalize_date(docket_date),
        "case_title": case_title.strip() if case_title else None,
        "lower_court": lower_court.strip() if lower_court else None,
        "lower_court_case_numbers_raw": lower_court_case_numbers_raw.strip()
        if lower_court_case_numbers_raw
        else None,
        "lower_court_case_numbers": clean_lower_court_cases(
            lower_court_case_numbers_raw
        )
        if lower_court_case_numbers_raw
        else None,
        "lower_court_decision_date": normalize_date(lower_court_decision_date),
        "lower_court_rehearing_denied_date": normalize_date(
            lower_court_rehearing_denied_date
        ),
    }


def parse_json_file(file_path):  # Example: 16-1362
    """Parse the JSON file and extract relevant fields."""
    with open(file_path, "r") as file:
        json_data = json.load(file)

    # Extract relevant fields from the JSON data
    docket_number = json_data.get("CaseNumber", "")
    docket_date = json_data.get("DocketedDate", "")
    case_title = f"{json_data.get('PetitionerTitle', '')} v. {json_data.get('RespondentTitle', '')}"
    lower_court = json_data.get("LowerCourt", "")
    lower_court_case_numbers_raw = json_data.get("LowerCourtCaseNumbers", "")
    lower_court_decision_date = json_data.get("LowerCourtDecision", "")
    lower_court_rehearing_denied_date = json_data.get("LowerCourtRehearingDenied", "")

    return format_scotus_metadata(
        docket_number=docket_number,
        docket_date=docket_date,
        case_title=case_title,
        lower_court=lower_court,
        lower_court_case_numbers_raw=lower_court_case_numbers_raw,
        lower_court_decision_date=lower_court_decision_date,
        lower_court_rehearing_denied_date=lower_court_rehearing_denied_date,
    )


def parse_html_file(file_path):  # Example: 16-1014
    """Parse the HTML file and extract relevant fields."""
    with open(file_path, "r") as file:
        html = file.read()

    # Use BeautifulSoup to parse the HTML content
    soup = BeautifulSoup(html, "html.parser")

    def _get_text_after_label(label):
        tag = soup.find("td", string=lambda s: s and s.strip().startswith(label))
        if tag and tag.find_next_sibling("td"):
            return tag.find_next_sibling("td").get_text(strip=True) or None
        return None

    def _get_docket_number():
        td = soup.find("td", class_="InfoTitle")
        if td:
            span = td.find("span", class_="DocketInfoTitle")
            if span:
                return span.get_text(strip=True).replace("No. ", "") or None
        return None

    def _get_case_title():
        title_span = soup.find("span", class_="title")
        if title_span:
            return " ".join(title_span.stripped_strings)
        return None

    docket_number = _get_docket_number()
    docket_date = _get_text_after_label("Docketed:")
    case_title = _get_case_title()
    lower_court = _get_text_after_label("Lower Ct:")
    lower_court_case_numbers_raw = _get_text_after_label("Case Numbers:")
    lower_court_decision_date = _get_text_after_label("Decision Date:")
    lower_court_rehearing_denied_date = _get_text_after_label("Rehearing Denied:")

    return format_scotus_metadata(
        docket_number=docket_number,
        docket_date=docket_date,
        case_title=case_title,
        lower_court=lower_court,
        lower_court_case_numbers_raw=lower_court_case_numbers_raw,
        lower_court_decision_date=lower_court_decision_date,
        lower_court_rehearing_denied_date=lower_court_rehearing_denied_date,
    )


def parse_well_formed_htm(soup):  # Example: 03-6084
    def _get_text_after_label(label):
        td = soup.find("td", string=lambda x: x and x.strip().startswith(label))
        if td:
            next_td = td.find_next_sibling("td")
            return next_td.get_text(strip=True) if next_td else None
        return None

    # Extract docket number from the "No. xx-xxxx" cell
    docket_number_td = soup.find(
        "td", string=lambda x: x and x.strip().startswith("No.")
    )
    docket_number = (
        docket_number_td.get_text(strip=True).replace("No. ", "")
        if docket_number_td
        else None
    )

    # Extract title from nested table after the "Title:" label
    def _get_case_title():
        title_td = soup.find(
            "td", string=lambda x: x and x.strip().startswith("Title:")
        )
        if title_td:
            nested_table = title_td.find_next_sibling("td").find("table")
            if nested_table:
                lines = [line.strip() for line in nested_table.stripped_strings]
                return " ".join(lines)

    docket_date = _get_text_after_label("Docketed:")
    case_title = _get_case_title()
    lower_court = _get_text_after_label("Lower Ct:")
    lower_court_case_numbers_raw = _get_text_after_label("Case Nos.:")
    lower_court_decision_date = _get_text_after_label("Decision Date:")
    lower_court_rehearing_denied_date = _get_text_after_label("Rehearing Denied:")

    return format_scotus_metadata(
        docket_number=docket_number,
        docket_date=docket_date,
        case_title=case_title,
        lower_court=lower_court,
        lower_court_case_numbers_raw=lower_court_case_numbers_raw,
        lower_court_decision_date=lower_court_decision_date,
        lower_court_rehearing_denied_date=lower_court_rehearing_denied_date,
    )


def parse_malformed_htm(soup):  # Example: 00-1753
    # Extract docket number from the "No. xx-xxxx" cell
    docket_number_td = soup.find(
        "td", string=lambda x: x and x.strip().startswith("No.")
    )
    docket_number = (
        docket_number_td.get_text(strip=True).replace("No. ", "")
        if docket_number_td
        else None
    )

    # Extract case name
    def _get_case_title():
        title_td = soup.find(
            "td", string=lambda x: x and x.strip().startswith("Title:")
        )
        if title_td:
            sibling_td = title_td.find_next_sibling("td")
            if sibling_td:
                lines = []
                lines.extend(sibling_td.stripped_strings)
                for next_td in sibling_td.find_all_next("td"):
                    if next_td.get_text(strip=True).startswith("Docketed"):
                        break
                    lines.extend(next_td.stripped_strings)
                if lines:
                    return " ".join(lines)

    def _get_malformed_info_format_2():
        lower_court = docket_date = lower_court_case_numbers_raw = None

        # Get docketed date
        docketed_td = soup.find("td", string=lambda x: x and "Docketed:" in x)
        if docketed_td:
            sibling = docketed_td.find_next_sibling("td")
            if sibling:
                docket_date = sibling.get_text(strip=True)

        # Get lower court
        lower_ct_td = soup.find("td", string=lambda x: x and "Lower Ct:" in x)
        if lower_ct_td:
            sibling = lower_ct_td.find_next_sibling("td")
            if sibling:
                lower_court = sibling.get_text(strip=True)

        # Get lower court case numbers
        case_nos_td = soup.find("td", string=lambda x: x and "Case Nos.:" in x)
        if case_nos_td:
            sibling = case_nos_td.find_next_sibling("td")
            if sibling:
                lower_court_case_numbers_raw = sibling.get_text(strip=True)

        return lower_court, docket_date, lower_court_case_numbers_raw


    # Extract docketed date and lower court information from malformed structure
    def _get_malformed_info():
        lower_court = docket_date = lower_court_case_numbers_raw = None

        # Find the <td> that contains "Lower Ct:"
        lower_ct_td = soup.find("td", string=lambda x: x and "Lower Ct:" in x)
        if lower_ct_td:
            # Row 1: "Lower Ct:" is in the same row as lower court
            row1 = lower_ct_td.find_parent("tr")
            tds_row1 = row1.find_all("td")
            if len(tds_row1) >= 3:
                lower_court = tds_row1[2].get_text(strip=True)

            # Row 2 is the next sibling <tr>
            row2 = row1.find_next_sibling("tr")
            if row2:
                tds_row2 = row2.find_all("td")
                if len(tds_row2) >= 1:
                    docket_date = tds_row2[0].get_text(strip=True)
                if len(tds_row2) >= 3:
                    lower_court_case_numbers_raw = tds_row2[2].get_text(strip=True)

        if docket_date is None or "Case No" in docket_date: # Example: 99-478.htm
            # If we didn't find the docket date in the expected place, try another method
            print("trying to extract malformed info format 2")
            lower_court, docket_date, lower_court_case_numbers_raw = _get_malformed_info_format_2()

        return lower_court, docket_date, lower_court_case_numbers_raw

    case_title = _get_case_title()
    lower_court, docket_date, lower_court_case_numbers_raw = _get_malformed_info()
    lower_court_decision_date = None
    lower_court_rehearing_denied_date = None

    return {
        "case_number": docket_number if docket_number else None,
        "docket_date": normalize_date(docket_date),
        "case_title": case_title.strip() if case_title else None,
        "lower_court": lower_court if lower_court else None,
        "lower_court_case_numbers_raw": lower_court_case_numbers_raw
        if lower_court_case_numbers_raw
        else None,
        "lower_court_case_numbers": clean_lower_court_cases(
            lower_court_case_numbers_raw
        )
        if lower_court_case_numbers_raw
        else None,
        "lower_court_decision_date": normalize_date(lower_court_decision_date),
        "lower_court_rehearing_denied_date": normalize_date(
            lower_court_rehearing_denied_date
        ),
    }


def parse_htm_file(file_path):
    """Parse the HTM file and extract relevant fields."""
    with open(file_path, "r") as file:
        html = file.read()

    # Use BeautifulSoup to parse the HTML content
    soup = BeautifulSoup(html, "html.parser")

    # Check for <meta> tags inside <body> to determine if the HTM is well-formed
    body = soup.find("body")
    is_well_formed = bool(body and body.find("meta"))

    if is_well_formed:
        return parse_well_formed_htm(soup)
    else:
        return parse_malformed_htm(soup)


def parse_scotus_file(file_path):
    """Determine the file type and parse accordingly."""
    file_path = Path(file_path)

    if file_path.suffix == ".json":
        return parse_json_file(file_path)
    elif file_path.suffix == ".html":
        return parse_html_file(file_path)
    elif file_path.suffix == ".htm":
        return parse_htm_file(file_path)
    else:
        raise ValueError(f"Unsupported file type: {file_path.suffix}")
