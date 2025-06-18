from bs4 import BeautifulSoup
from natsort import natsorted
import csv
import os
import re
from datetime import datetime
import pandas as pd
from app import *
from court_utils import *


def clean_text(raw_text):
    # Case 1: Slash-separated numbers with the same prefix: "98-35309/35509" or  "96-C-235/239/240/241"
    if re.match(r'^\d+-[A-Za-z]*-?\d+(/\d+)+$', raw_text):
        parts = raw_text.split('/')
        cleaned_parts = []
        
        # Extract the prefix from the first part
        prefix_match = re.match(r'(.*-)(\d+)$', parts[0])
        if prefix_match:
            current_prefix = prefix_match.group(1)
            first_number = prefix_match.group(2)
            cleaned_parts.append(f"{current_prefix}{first_number}")
            
            # Apply the prefix to all subsequent parts
            for part in parts[1:]:
                # Remove any leading/trailing whitespace
                part = part.strip()
                # If part is a number without prefix, add the current prefix
                if re.match(r'^\d+$', part):
                    cleaned_parts.append(f"{current_prefix}{part}")
                else:
                    # If part has its own prefix, use it as is
                    cleaned_parts.append(part)
            return ', '.join(cleaned_parts)
        else:
            # If prefix extraction fails, return the raw text
            return raw_text
    
    # Case 2: Various separators with inherited prefixes: "98-4033;-4214;-4246"
    if re.match(r'^\d+-\d+([;/,-]-\d+)+$', raw_text):
        parts = re.split(r'[;/,-]', raw_text)
        prefix = parts[0].split('-')[0]
        cleaned_parts = [f"{prefix}-{part.strip('-')}" for part in parts if part.strip('-') and part.strip('-') != prefix]
        return ', '.join(cleaned_parts)

   # Case 3: Negative prefix-separated numbers: "99-1845,-1846,-1847,-197" or "98-60240,-60454,-60467,-""
    if re.match(r'^\d+-\d+(,-\d+)*,-?$', raw_text):
        prefix_match = re.match(r'(\d+-)(\d+)', raw_text)
        if prefix_match:
            current_prefix = prefix_match.group(1)
            remaining_text = raw_text[len(prefix_match.group(0)):]
            numbers = [prefix_match.group(2)] + remaining_text.split(',')
            # Exclude empty strings resulting from trailing hyphens
            cleaned_parts = [f"{current_prefix}{num.strip('-')}" for num in numbers if num.strip('-')]
            return ', '.join(cleaned_parts)
        else:
            return raw_text

    # Case 4: Comma-separated items with prefixes already correct: "33094-CW, 33095-CW"
    if re.match(r'^(\d+-[A-Za-z]+, )*\d+-[A-Za-z]+$', raw_text):
        return raw_text

    # Case 5: Mixed slash-separated values with a shared prefix: "98-16950/17044/17137"
    if re.match(r'^\d+-\d+(/\d+)+$', raw_text):
        parts = raw_text.split('/')
        prefix = parts[0].split('-')[0]
        cleaned_parts = [f"{prefix}-{part.split('-')[-1]}" for part in parts]
        return ', '.join(cleaned_parts)

    # Case 6: Ampersand-separated with prefixes: "95-56639 & 96-55194"
    if '&' in raw_text:
        return raw_text.replace(' & ', ', ')

    # Case 7: Preserve specific formats: "CR-99-1140", "1998-CA-0022039-MR"
    if re.match(r'^(CR|CA)-\d+-\d+$', raw_text) or re.match(r'^\d+-\d+-\d+-[A-Za-z]+$', raw_text):
        return raw_text

    # Case 8: Range expansion: "97-1715/98-1111 to 1115" or "97-1715/1111 to 1115"
    if re.search(r'\d+/\d+(-\d+)? to \d+', raw_text):
        parts = raw_text.split('/')
        cleaned_parts = [parts[0]]  # Start with the first part, which has the full prefix

        # Process the range part
        range_part = parts[1]

        # Check if the range start has a different prefix than the main prefix
        if ' to ' in range_part:
            start_part, end_part = range_part.split(' to ')
            if '-' in start_part:  # Different prefix specified in the range
                start_prefix, start_number = re.match(r'(\d+)-(\d+)', start_part).groups()
                if '-' in end_part:  # Full end prefix given
                    end_prefix, end_number = re.match(r'(\d+)-(\d+)', end_part).groups()
                    # Expand with the specified prefixes
                    cleaned_parts.append(f"{start_prefix}-{start_number}")
                    if start_prefix == end_prefix:
                        cleaned_parts.extend([f"{start_prefix}-{i}" for i in range(int(start_number) + 1, int(end_number) + 1)])
                    else:
                        cleaned_parts.extend([f"{end_prefix}-{i}" for i in range(int(start_number) + 1, int(end_number) + 1)])
                else:  # Inherit prefix only for the end of the range
                    end_number = int(end_part)
                    cleaned_parts.append(f"{start_prefix}-{start_number}")
                    cleaned_parts.extend([f"{start_prefix}-{i}" for i in range(int(start_number) + 1, end_number + 1)])
            else:
                # If no new prefix in range start, inherit prefix from the first part
                prefix = parts[0].split('-')[0]
                start, end = map(int, range_part.split(' to '))
                cleaned_parts.extend([f"{prefix}-{i}" for i in range(start, end + 1)])
        else:
            # No range detected, handle as simple case
            cleaned_parts.append(f"{parts[0].split('-')[0]}-{range_part.strip()}")
        
        return ', '.join(cleaned_parts)

    # Case 9: Various separators with inherited prefixes: "98-4033;-4214;-4246"
    if re.match(r'^\d+-\d+([;/,-]-\d+)+$', raw_text):
        parts = re.split(r'[;/,-]', raw_text)
        prefix = parts[0].split('-')[0]
        cleaned_parts = [f"{prefix}-{part.strip('-')}" for part in parts]
        return ', '.join(cleaned_parts)

    # Case 10: Negative prefix-separated numbers: "99-1845,-1846,-1847,-197"
    if re.match(r'^\d+-\d+(,-\d+)+$', raw_text):
        prefix = raw_text.split('-')[0]
        numbers = re.split(r',', raw_text)
        cleaned_parts = [f"{prefix}-{num.strip('-')}" for num in numbers]
        return ', '.join(cleaned_parts)

    # General replacement for remaining slashes, semicolons, or ampersands
    text = re.sub(r'[/;&]', ',', raw_text)

    # Clean up any extra spaces and return the result
    return ', '.join(part.strip() for part in text.split(','))

    '''
    # Test cases
    test_inputs = [
        "96-C-235/239/240/241",
        "99-1845,-1846,-1847,-197",
        "98-35309/35509",
        "33094-CW, 33095-CW",
        "98-16950/17044/17137",
        "95-56639 & 96-55194",
        "CR-99-1140",
        "98-6317/6319/99-5125",
        "98-4033;-4214;-4246",
        "1998-CA-0022039-MR",
        "48601-6-II",
        "05-15-00956-CV",
        "4-15-0436",
        "15-4366, 15-4379",
        "15-50534, 15-50535, 15-50549",
        "14-16560, 14-16641",
        "15-2691-bk, 15-2962-bk, 15-2971-bk",
        "97-1715/98-1111 to 1115/",
        "97-1715/1111 to 1115",
        "98-60240,-60454,-60467,-"
    ]

    # Testing outputs
    for raw_text in test_inputs:
        cleaned_output = clean_text(raw_text)
        print(f"Raw: {raw_text} -> Cleaned: {cleaned_output}")
    '''

def parseXML(htmlFile):
    with open(htmlFile) as fobj:
        html = fobj.read()

    soup = BeautifulSoup(html, 'html.parser')

    # IIRC the regex substring matching is because of <span> tags that may or may not be present within the cell
    sup_case = soup.find('title', text=re.compile(r'^Docket for', re.IGNORECASE))
    lower_ct = soup.find('td', text=re.compile(r'^Lower Ct', re.IGNORECASE))

    if sup_case is not None and lower_ct is not None:
        # the label here is not consistent Case Nos. (no span) vs Case Numbers (in a span), and there can be...
        # numerous preceding &nbsp; characters. Grabbing the next cell after lower_ct with parens seems to...
        # work well
        case_nos_original = lower_ct.findNext('td', text=re.compile(r'^\s*\(.*\)\s*$'))

        if case_nos_original is None:
            return []

        # this probably isn't useful, but seeing as it's easy to stick in a column....
        if 'Capital Case' in sup_case:
            capital_case = 'Y'
        else:
            capital_case = 'N'

        sup_case = sup_case.text.replace('Docket for ', '').replace('*** CAPITAL CASE ***', '').strip()
        year = int(sup_case[:2]) 

        if htmlFile.endswith('.html'):
            link = 'https://www.supremecourt.gov/search.aspx?filename=/docket/docketfiles/html/public/' + sup_case + '.html'
        else:
            link = 'https://www.supremecourt.gov/search.aspx?filename=/docketfiles/' + sup_case + '.htm'

        scotus_docket_no = '=HYPERLINK("' + link + '", "' + sup_case + '")'

        # Docketed is the date the docket was assigned, but it's a real mess, sometimes labelled but missing,...
        # sometimes labelled but with the actual value in a lower-row cell, after other labels/values are shown
        docketed = soup.find('td', text=re.compile(r'^Docketed', re.IGNORECASE))
        docketed = docketed.find_next_sibling("td")
        if 'Lower Ct' in docketed.text.strip():
            docketed = docketed.findNext("tr").findNext("td").text.strip()
        else:
            docketed = docketed.text.strip()

         # some A dockets link to another SCOTUS docket. The number is in the cell, like "Linked with YY-1234"
        linked_with = soup.find('td', text=re.compile(r'^Linked with', re.IGNORECASE))
        if linked_with is not None : linked_with = linked_with.text.replace('Linked with ', '').strip()
        
        lower_ct = lower_ct.find_next_sibling("td").text.strip()

        case_nos_original = case_nos_original.text.replace('(', '').replace(')', '').strip()
        case_nos_original = ' '.join(case_nos_original.split())

        case_nos = clean_text(case_nos_original)

        return [scotus_docket_no, docketed, capital_case, linked_with, lower_ct, case_nos_original, case_nos]
    else:
        return []
