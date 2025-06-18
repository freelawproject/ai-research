# Summarization Tools
As of June 18, 2025

## Legal Background
Legal cases often involve a large number of documents, of many types, that require subject matter expertise to understand. Summarization makes this information more manageable and accessible for researchers, advocates, and legal professionals. But it is extremely time- and expertise-intensive. Effective and accurate AI summarization would be a huge step forward.

## Goal
Develop summarization tools for the legal domain that accurately and efficiently condense complex litigation documents, including court-generated opinions, orders, and indexes (docket sheets), and party-generated filings.

These three focus areas were designed to advance towards the overarching goal.
1. **Long to Micro Summarization:** Make legal case summaries shorter and easier to digest. This can be done by taking existing long form, human written summaries (written by Clearinghouse students) into a micro summary format that allows users to quickly understand the stakes and subject matter of a case.

2. **Case/Multi Document Summarization:** Lawsuits typically involve multiple documents. Develop a system that can combine information from several sources into a single, cohesive case summary. The system can dynamically update summaries as new documents are filed, further streamlining the research process.

3. **Evaluation Methods:** Support a robust evaluation method to ensure accuracy and efficiency. This is especially critical in the legal domain where even small errors can have big impacts.

### Long to Micro Summarization
This work was done in conjunction with Dr. Eytan Adar (Associate Professor in School of Information and Associate Professor of Electrical Engineering and Computer Science at University of Michigan).

#### Experimental Design and Experiments
A dataset of approximately 10,000 case summaries written by Clearinghouse students was used.  These summaries varied in length, complexity, and style which made them ideal for testing the summarization technique. We designed a series of prompts aimed at producing clear, brief summaries that still captured essential legal facts. These prompts were used with Claude 3.5 Sonnet using a scripted workflow that prompted the model iteratively until a target summary length was achieved.

#### Example Result:
**Existing Case Summary:**
This investigation and subsequent settlement agreement involved discrimination against individuals with Hepatitis C (HCV) and substance abuse disorder (SUD) due to non-medically indicated sobriety restrictions for HCV treatment. On December 5, 2022, the Special Litigation section of the U.S. Department of Justice’s Civil Rights Division (DOJ) announced that it had secured a settlement agreement with the state of Alabama’s Medicaid Agency to ensure that Alabama’s Medicaid recipients with HCV who also struggled with SUD had equal access to medication to treat their hepatitis, in compliance with Title II of the Americans with Disabilities Act (ADA). The DOJ found that Alabama Medicaid’s policy of requiring sobriety discriminated against individuals based on their disability status.

The settlement agreement was initiated following an investigation into complaints that Alabama Medicaid’s sobriety policy discriminated against people with HCV and SUD by requiring individuals to abstain from alcohol and illicit drugs for six months to be eligible for Direct-Acting Antiviral agents (DAA) treatment. The United States argued that the policy was non-medically indicated and constituted a violation of Title II of the ADA by denying essential care on the basis of disability.

Alabama Medicaid cooperated with the DOJ investigation while denying any wrongdoing or violation of Title II of the ADA. Nevertheless, both parties agreed to resolve the matter through a settlement agreement to ensure individuals with HCV were not subjected to the sobriety policy and received the necessary medical treatment without discrimination.

The agreement outlined that Title II of the ADA prohibits discrimination against qualified individuals with disabilities by public entities, which includes services provided under Medicaid like medical evaluation, screening, treatment, and medication. It asserted that Alabama Medicaid was subject to Title II and its implementing regulations as a public entity.

Under the terms of relief, the agreement stipulated that Alabama Medicaid would withdraw the sobriety policy effective October 1, 2022, ensuring no future denial, delay, or non-payment for DAA treatment based on a recipient’s drug or alcohol use. Alabama Medicaid committed to no longer imposing drug or alcohol use as a prerequisite for DAA treatment or establishing any new restrictions related to drug or alcohol use. It must notify all Medicaid providers, certain targeted providers, and critical state partners about withdrawing the sobriety policy and update treatment protocols accordingly.

The agreement also mandated that Alabama Medicaid notify Medicaid recipients about the policy change, encouraging them to seek HCV screening and treatment regardless of their SUD status. A website notification must be posted to inform the public about the expanded availability of HCV treatment without the sobriety policy. To ensure compliance and monitor the effective implementation of these changes, Alabama Medicaid was tasked with preparing and providing two reports to the DOJ detailing actions taken to withdraw the sobriety policy, review and remedy any denials based on drug or alcohol use, and address any complaints related to access to DAA treatment.

The agreement was set to remain in effect for eighteen months from its effective date, with a defined dispute resolution process to address any non-compliance or disputes related to its terms. The implementation and monitoring of the settlement agreement remain ongoing.

**Shortened Case Summary:** DOJ disability discrimination case against Alabama Medicaid settled with revised hepatitis treatment policies.

#### Conclusion:
Initial review by subject matter expert Margo Schlanger confirmed that several dozen summaries appeared accurate. We are currently working to integrate this approach into the Clearinghouse’s workflow, with a human-in-the-loop step to ensure accuracy going forward. 

#### Files
- baseprompt.txt, secondaryprompt.txt: Prompts utilized to generate micro summaries
- case_long_to_micro_summarizer.py: Main script
- long_to_micro_results.txt: Results

### Multi Document Summarization
This work was done in conjunction with Professor Charlotte Alexander, Professor of Law and Ethics at Georgia Tech.

#### Experimental Design and Experiments
Gemini 2.0 Flash LLM was used within an agentic workflow to generate legal summaries. The documents were organized into categories such as complaints, settlements, and docket sheets. The first step was to generate summaries for individual documents to assess how each effective it was and how it contributed to its case understanding. This helped to lay the groundwork of a more structured, multi document summarization framework.

During the multi document summarization experiment, one major takeaway was that summary quality depended heavily on both the type of documents and the order in which they were presented to the model. This reinforced the idea that legal summarization isn’t just about condensing text as it requires a strong understanding of context and structure.

So we tried using docket sheets as the backbone for generating summaries. But that approach quickly ran into problems. The documents were being added without much structure which made it hard to create a clear, meaningful summary. It became obvious that we needed a more intentional strategy, one that takes into account the role each document plays and how it fits into the bigger picture.

#### Conclusion:
For summaries to be reliable and useful, we need a structured approach that uses the docket as a roadmap and brings in other documents based on their type and relevance. This strategy should build a clear narrative foundation, iteratively add in legal details as new information comes in, and allow for ongoing improvements through user feedback.

#### Next steps
- Identify which types of documents should be labeled and understand how each contributes to the overall summary.
- Test the same summarization setup using a different LLM to compare performance and accuracy.
- Explore the addition of a chatbot interface to allow users to interact with summaries and extract further insights.

### Evaluation Method
This work was done in conjunction with Dr. Lu Wang, Associate Professor in the School of Computer Science and Engineering at University of Michigan, and Jie Ruan, a PhD candidate in Computer Science and Engineering at the University of Michigan. The work is part of the paper “ExpertLongBench: Benchmarking Language Models on Expert-Level Long-Form Generation Tasks with Structured Checklists” by Jie Ruan, et al, submitted to the NeurIPS 2025 Datasets and Benchmarks Track.

#### Experimental Design and Experiments
Create a rubric to evaluate model performance on its summarizations using human-in-the-loop or human-generated comparators. Once a summarization approach is sufficiently successful, then we can use it to generate summaries where there is no human comparator.  

The first attempt followed this methodology.:
- Generate Summary (Product A): Use an LLM to produce a summary of the legal case.
- Extract Facts from Human Written Summary (Product B): Convert key facts from a human written summary into a structured rubric format.
- Validate Extraction Quality: Apply rubric to ensure that Product B accurately and comprehensively reflects the human summary.
- Extract Facts from LLM Summary (Product C): Apply the same rubric to extract key facts from the LLM generated summary.
- Compare Product B and Product C: Assess the LLM summary by comparing its extracted facts to those from the human summary, evaluating completeness and factual consistency.

Evaluate how successful different LLM are at assessing the quality of legal summaries. 3 open weight (Llama [28] 8B, 70B; Mistral 12B, 123B; 222 and Qwen [29] 7B, 72B) and 3 proprietary families (GPT-4o, GPT-4o-mini; Claude-3.5-Haiku,Claude-3.7-Sonnet; and Gemini-2.0-Flash) were utilized. 

Among the models tested, Gemini-2.0-Flash was the best performing but still scored low in its F1 Score (a metric that weights precision and recall to measure the accuracy and completeness of the output). These results show that LLMs still face major limitations when it comes to evaluating summarizing complex litigation content.

#### Example of items in Rubric:
**Content Checklist**
- Filing Date
- Class Action or Individual Plaintiffs? (if applicable)
- Cause of Action
- Statutory or Constitutional Basis for the Case
- Remedy Sought 
- Who are the parties (description, not name)?
- Type of Counsel
- Consolidated cases noted (if applicable)
- Related Cases listed by their case code number (if applicable)
- Note important filings (if applicable)
- All reported opinions cited with shortened Bluebook citation (if applicable)
- First and Last name of Judge
- Significant Terms of decrees (if applicable)
- Dates of all decrees (if applicable)
- How long decrees will last (if applicable)
- Significant Terms of settlement (if applicable)
- Date of settlement (if applicable)
- How long settlement will last (if applicable)
- Whether the settlement is court-enforced or not (if applicable)
- Was there a monitor? Note the name of the monitor. (if applicable)
- Monitor's Reports (if applicable)
- Appeal (if applicable)
- Trials (if applicable)
- Court rulings on any of the important filings (if applicable)
- Factual basis of case
- Disputes over settlement enforcement (if applicable)

**Style Checklist**:
- Write in the Past Tense
- Explain the case in temporal order
- Does the summary explain the case chronologically? yes/no
- The summary should contain an opening sentence that describes the general topic of the case 
- The summary should contain a final sentence about the current status of the case 
- Don’t name any people

#### Conclusion:
This approach of using a structured rubric would give us a consistent and scalable way to evaluate summaries. By pulling out key facts from both human written and AI generated summaries and organizing them into the same format, we can more easily compare how complete and accurate each version is. 

#### Next steps:
- Test the methods using a different LLMs to compare performance and accuracy
- Try different rubrics and the length of rubrics
- If this evaluation method does not work, return to human-in-the-loop methods.