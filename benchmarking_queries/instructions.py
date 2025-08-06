prompt = """
You will be given a list of keywords. Your task is to use creative writing to transform these keywords into a natural language query that reflects their semantics. The keywords do not need to appear verbatim in the query, but the query must clearly convey the meaning or implications of all the keywords.

You will also be given a list of included words. These words must appear in the query, and each must be enclosed in quotation marks.

The query should:
- Be framed within a legal context, specifically something a user might realistically search for when looking up U.S. case law or legal issues in the United States.
- Be no more than 1000 characters.
- Take the form of a descriptive statement (e.g., a fictional or real-world scenario involving the keywords) or a question (e.g., asking for a definition or application of the keywords).
- Be expressive and potentially elaborate, allowing for creative or hypothetical situations, even if not directly tied to the keywords — as long as their semantics are preserved.

Return the result as a JSON object with the key "query" and the natural language query as the value.

Examples:

keywords: ["eviction", "California"]
included_words: ["eviction"]
response: {"query": "I am a student living in California, my landlord has just sent me an 'eviction' notice, saying I must leave by the end of the month because they are selling the house. How can they do this to me, I have nowhere to go!"}

keywords: ["copyright"]
included_words: []
response: {"query": "What is copyright?"}
"""
