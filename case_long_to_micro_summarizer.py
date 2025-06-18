from dotenv import dotenv_values
from anthropic import Anthropic
import pandas as pd
import re
import time

MAXCHARS = 120
CASELIMIT = 4000

config = dotenv_values(".env")

client = Anthropic(
    api_key=config["ANTHROPIC_API_KEY"],  # read the key
)

# load content of baseprompt.txt (this is the prompt for the first message)
with open("baseprompt.txt", "r") as file:
    baseprompt = file.read()

# load content of secondaryprompt.txt (this is the prompt for the second message, used if the first summary is too long)
with open("secondaryprompt.txt", "r") as file:
    secondaryprompt = file.read()

# read the Micro-summary.csv file (this is the input file)  
df = pd.read_csv("case_summaries.csv")

# read long_to_micro_results.txt (tab delimited), read the first column as a list, we're going to 
# keep track of the cases we've already processed and not run them again
with open("long_to_micro_results.txt", "r") as file:
    done = file.read().splitlines()
    done = [int(line.split("\t")[0]) for line in done]

# open a file to append the long_to_micro_results 
with open("long_to_micro_results.txt", "a") as file:

    # initialize a counter to keep track of the number of cases we've processed
    counter = 0

    # iterate over the rows of the dataframe
    for index, row in df.iterrows():
        case = row['id']
        if int(case) in done:  # skip if we've already processed this case
            continue

        summary =str(row['summary'])  # get the summary for this case

        if (len(summary) < 20): # if the input summary is too short, skip the case
            continue

        # replace <p> with newlines
        summary = summary.replace("<p>", "\n")

        # remove all html tags
        summary = re.sub(r'<.*?>', '', summary)

        counter += 1
        if (CASELIMIT != -1):
            if (counter > CASELIMIT):
                break

        # send the summary to the LLM, the system prompt is in baseprompt.txt
        message = client.messages.create(
            max_tokens=1024,
            system=baseprompt,
            messages=[
                {
                    "role": "user",
                    "content": summary,
                }
            ],
            model="claude-3-5-sonnet-latest",
        )

        # get the response from the LLM
        response = message.content[0].text

        # initialize the secondary response to an empty string
        response2 = ""

        # if the summary is longer than the maximum number of characters, we'll use the secondary prompt to 
        # get a better summary
        if (len(response) > MAXCHARS):
            # sleep 2 seconds
            time.sleep(2)

            # send the summary to the LLM again, the system prompt is in secondaryprompt.txt
            message2 = client.messages.create(
                max_tokens=1024,
                system=baseprompt,
                messages=[
                        {
                            "role": "user",
                            "content": summary,
                        },
                        {
                            "role": "assistant",
                            "content": response,
                        },
                        {
                            "role": "user",
                            "content": secondaryprompt,
                        },
                    ],
                model="claude-3-5-sonnet-latest",
            )
            response2 = message2.content[0].text
            
        # write the case, the first response, and the secondary response to the long_to_micro_results file
        file.write(str(case)+"\t"+response+"\t"+response2+"\n")
        file.flush()

        # write to the screen so we can track what's going on
        print(case,"\t",response,"\t",response2)

        # sleep for 2 seconds to avoid overwhelming the API
        time.sleep(2)

# close the long_to_micro_results file
file.close()
