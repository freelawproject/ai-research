# Case Tracing
As of June 18, 2025

## Legal Background
Right now, there’s no easy or consistent way to trace the path of a legal case as it moves through the court system (from trial court to appellate court and back, or perhaps to Supreme Court). Each court uses its own naming conventions (for both cases and their identifying docket numbers) and there’s no standard system that ties them all together.

Our system aims to account for these variations through contextual checks and validation at each level of the chain.

## Goal
Develop an advanced docket chaining and categorization, to facilitate tracking of appeals and their outcomes, including up to the Supreme Court.

#### Design and Implementation
To get started, we built a set of modules using Clearinghouse dockets as the foundation. Here's how the system came together:

1. **Scrape SCOTUS dockets:** We developed a script (scotus_scraper.py) to pull in Supreme Court docket data from 2000 to the present. This forms the top layer of the docket chain.

2. **Retrieve FJC Appellate Data**:We also incorporated data from the Federal Judicial Center (FJC IDB), which provides appellate-level details. This helps fill in the middle layer of the chain.

3. **Normalizing Court Names and Docket Formats**: 
This was one of the biggest challenges. Court names, circuits, and states are labeled differently across Clearinghouse, FJC, and SCOTUS datasets. 

For example:
- In Clearinghouse: Central District of California
- In FJC: CALIFORNIA CENTRAL

To unify these, we created a comprehensive mapping system (court_utils.py) to normalize names across sources.

Docket numbers in SCOTUS can appear in wildly different formats. Here’s an example where all of the following represent the same set of dockets:
- 98-16950/17044/17137
- 98-16950;-17044;-17137
- 98-16950 & 98-17044 & 98-17137
- 98-16950,-17044,-17137
We had to build a lookup table (scotus_scraper.py) that could handle these variations and reliably connect them. 

4. **Daisy Chaining Dockets from Bottom up**: Starting from a Clearinghouse docket, we map it up to FJC data and then to SCOTUS records. This isn’t a simple match because multiple courts can reuse similar numbering systems, so we need to include validation checks to avoid incorrect links.

Example:
- http://clearinghouse.net/case/17456 has docket  4:19-cv-00226, in the U.S. District Court for the Southern District of Texas.
- This maps to FJC numbering 24-20005 in the United States Court of Appeals for the Fifth Circuit
– This is the same case as 24-45 in the SCOTUS (https://www.supremecourt.gov/search.aspx?filename=/docket/docketfiles/html/public/24-45.html)

5. Daisy Chaining Dockets from Top Down: We also reverse the process (starting from SCOTUS and tracing downward) to double-check the connections and validate the system's accuracy.

6. Categorize and facilitate tracking of appeals and their outcomes: With the structure in place, we can now categorize cases and follow how they progress through the appeals system. This will give us a clearer picture of legal outcomes and how they shift across the lifecycle of a case.

#### Progress
Done with 1 to 4, 5 is in progress. 6 is next. 

#### Conclusion
This advanced chaining and categorization system will make it possible to follow a case across multiple court levels, including the Supreme Court, by ingesting and linking dockets and documents at each stage. This lays the groundwork for comprehensive tracking of legal cases and their outcomes.

#### Files
- scotus_scraper.py
- court_utils.py