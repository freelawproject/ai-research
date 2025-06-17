from bs4 import BeautifulSoup
import courtlistener
from datetime import datetime
import logging
import os
from typing import Literal

from vertexai.generative_models import GenerativeModel, GenerationConfig
from google.api_core.exceptions import ResourceExhausted


PREDICT_PROMPT = """You are a top class legal analyst. You are asked to consider if a case was overruled or not.

A cited case is considered overruled, fully or partially, if ANY of the following conditions are true:
    1. A majority of the Court explicitly states that case has been overruled.
    2. The Court uses language that is functionally equivalent to explicitly overruling the case.
    3. The Court overrules or qualifies only part of the case.
    4. The Court overrules the case insofar as it applies to certain circumstances.
    5. The Court distinguishes the case's treatment of different legal principles.
    6. The Court states that the case is no longer good law in certain contexts.

A cited case is not considered overuled in all other cases. For example,
- 'followed': The citing case followed the cited case as precedent.
- 'mentioned': The citing case mentioned the cited case, but did not treat it as precedent.

The below excerpt contains snippets of a court opinion mentioning a case of interest.
Cites to this case will be in between XML tags <caseOfInterest> and </caseOfInterest>.
Analyze if the case of interest has been overruled or not.
If the snippets overrule a case, pay attention to whether it is actually the case of interest being overruled,
as they might be overruling a different case.

Please respond in this exact JSON format:
{
    "overruled": "yes" or "no",
    "reasoning": "Your detailed explanation",
    "confidence": int - 0 to 100 (representing percentage)
}
"""  # noqa: E501

# VertexAI Gemini
client = GenerativeModel(
    'gemini-1.5-flash-002',
    generation_config=GenerationConfig(response_mime_type="application/json"),
    system_instruction=PREDICT_PROMPT,
)


def get_citation_excerpts(opinion_obj: dict, cited_id: str) -> str:
    html_with_citations = opinion_obj['html_with_citations']
    soup = BeautifulSoup(html_with_citations, features='lxml')
    quotes = soup.find_all('span', attrs={'data-id': cited_id})
    if len(quotes) == 0:
        return ""
    for quote in quotes:
        quote.string = f"<caseOfInterest>{quote.text}</caseOfInterest>"

    excerpts = []
    index = -1
    indices = []
    while True:
        index = soup.text.find('<caseOfInterest>', index + 1)
        if index == -1:
            break
        final_index = soup.text.find('</caseOfInterest>', index + 1)
        if (index, final_index) not in indices:
            indices.append((index, final_index))

    for index, final_index in indices:
        excerpts.append((max(0, index - 1000), final_index + len('</caseOfInterest>') + 1000))

    joined_excerpts = []
    start, end = excerpts[0]
    joined_excerpt = (start, end)
    for start, end, in excerpts[1:]:
        if start > joined_excerpt[1]:
            joined_excerpts.append(joined_excerpt)
            joined_excerpt = (start, end)
        else:
            joined_excerpt = (
                joined_excerpt[0],
                end,
            )
    joined_excerpts.append(joined_excerpt)

    joined_excerpt_texts = []
    for joined_excerpt in joined_excerpts:
        start, end = joined_excerpt
        joined_excerpt_text = soup.text[start:end]
        joined_excerpt_texts.append(joined_excerpt_text)

    joined_excerpts_header = (
        f"Case of interest: {courtlistener.opinion_case_name(cited_id)}"
        '\n-----------------------\n'
    )
    joined_excerpts_text = '\n-----------------------\n'.join(joined_excerpt_texts)

    return joined_excerpts_header + joined_excerpts_text


def get_prediction(citing_id, cited_id, overruled, excerpts: str) -> dict:
    import json
    from time import sleep
    # Call LLM
    wait_s = 4
    retries = 0
    while retries < 10:
        try:
            completion = client.generate_content(contents=excerpts)
        except ResourceExhausted:
            print(f'Resource exhausted. Retrying in {wait_s}s ({retries}/10)')
            sleep(wait_s)
            wait_s *= 2
            retries += 1
        else:
            break

    llm_answer = completion.candidates[0].content.parts
    if not llm_answer:
        return {
            'citing_id': citing_id,
            'cited_id': cited_id,
            'overruled': overruled,
            'prediction': 'no',
            'reasoning': 'MODEL_ERROR',
            'confidence': -1,
        }
    else:
        structured_answer = json.loads(llm_answer[0].text)
    return {
        'citing_id': citing_id,
        'cited_id': cited_id,
        'overruled': overruled,
        'prediction': structured_answer['overruled'],
        'reasoning': structured_answer['reasoning'],
        'confidence': structured_answer['confidence'],
    }


def output_futures(futures, output_lines):
    from concurrent.futures import wait
    wait([fut for fut in futures if not isinstance(fut, dict)])
    for future in futures:
        if isinstance(future, dict):
            prediction = future
        else:
            prediction = future.result()
        output_lines.append(
            f"{prediction['citing_id']}"
            f"\t{prediction['cited_id']}"
            f"\t{prediction['overruled']}"
            f"\t{prediction['prediction'].lower()}"
            f"\t{prediction['confidence']}"
            f"\t{prediction['reasoning']}\n"
        )


def extract_all_excerpts():
    from concurrent.futures import ThreadPoolExecutor
    from time import sleep

    output_folder = f"prediction_{datetime.now().strftime('%Y-%m-%d_%H-%M')}"
    excerpts_folder = os.path.join(output_folder, 'excerpts')
    os.makedirs(excerpts_folder, exist_ok=True)

    with open('courtlistener_dataset_with_overrulings.csv', 'rt') as fd:
        dataset_lines = fd.readlines()

    output_lines = ['citing_id\tcited_id\toverruled\tprediction\tconfidence\treasoning\n']
    current_citing_id = None
    opinion_obj = None
    batch_size = 100
    futures = []
    with ThreadPoolExecutor() as executor:
        for i, line in enumerate(dataset_lines):
            if i == 0:
                continue
            fields = line[:-1].split('\t')
            depth = fields[2]
            citing_id = fields[3]
            cited_id = fields[4]
            overruled = fields[10]
            if int(depth) == 0:
                logging.info(f'cite not found: {i}')
                futures.append({
                    'citing_id': citing_id,
                    'cited_id': cited_id,
                    'overruled': overruled,
                    'prediction': 'no',
                    'reasoning': 'CITE_NOT_FOUND',
                    'confidence': -1,
                })
                continue

            if citing_id != current_citing_id:  # new citing opinion
                logging.info(f"getting opinion: {citing_id}")
                opinion_obj = courtlistener.get_opinion(citing_id)
                current_citing_id = citing_id

            excerpts_fname = os.path.join(excerpts_folder,
                                          f"{i:04d}.{citing_id}_cites_{cited_id}.txt")
            logging.info(f"extracting citing excerpts: {i} - {excerpts_fname}")

            excerpts = get_citation_excerpts(opinion_obj, cited_id)
            with open(excerpts_fname, 'wt') as excerpts_file:
                print(excerpts, file=excerpts_file)

            if excerpts:
                futures.append(executor.submit(get_prediction,
                                               citing_id, cited_id, overruled, excerpts))
                sleep(0.31)
            else:
                futures.append({
                    'citing_id': citing_id,
                    'cited_id': cited_id,
                    'overruled': overruled,
                    'prediction': 'no',
                    'reasoning': 'EMPTY_EXCERPTS',
                    'confidence': -1,
                })

            if i % batch_size == 0:
                logging.info('starting new batch...')
                output_futures(futures, output_lines)
                futures = []

        if futures:
            logging.info('processing last batch outputs...')
            output_futures(futures, output_lines)

    output_fname = os.path.join(output_folder, "output.csv")
    with open(output_fname, 'wt') as fd:
        fd.writelines(output_lines)


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    extract_all_excerpts()
