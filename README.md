# FLP_Metadata_Tagging

## Background
In the past, [Civil Rights Litigation Clearinghouse](https://clearinghouse.net/) utilizes law students to assign metadata to cases. Students follow the [instructions](https://docs.google.com/document/d/1tZt65Al3hWWzUu2m4AiiZ7X3Y58SqvKVoxz2OjNkhNs/edit?usp=drive_link) on Page 35 Section D to code the cases. Additional resources are [API documentation](https://api.clearinghouse.net/api-reference/objects/case) and [metadata submission form](https://clearinghouse.net/admin/edit/case/46274/details).

The goal is to utilize AI to automate some of these metadata tagging such that the fields are auto filled for human to review (instead of manually assign), to increase efficiency and allow the students more time to work on meaningful legal work. Ultimately, we foresee this as being fully automated with minimal human review required.

Margo Schlanger, the founder and director of the Clearinghouse noted that the metadata that takes the students the most amount of time are:
- [issues](https://api.clearinghouse.net/api-reference/objects/case/issues)
- [causes of action](https://api.clearinghouse.net/api-reference/objects/case/causes-of-action)
We believe these are metadata tags that could bring the most value, so we started on these. In addition, another easy win might be plaintiff/defendent classification (ie, individual, government, corporations, etc), which we will revisit in the next iteration of the project.

We obtained the case data from the Clearinghouse, encompassing ~10K human annotated cases that have either coding status "Coding Complete" or "Approved", indicating the human annotated metadata are final and ready to be used for model training and evaluation. The ~10K cases spanning 57 states/regions, covering ~195 courts, 27 case type, ~270 special collections, ~980 causes of action, ~6800 multi-label issues (of which there are ~405 unique issues), of which ~1/3 are ongoing cases while the rest are closed.

## Data Processing
To begin, we needed to obtain the docket information related to each case, we utilized the [Clearinghouse API](https://api.clearinghouse.net/how-to-guides) to retrieve the case information for each case. The data is saved in `0.clearinghouse_all_case_data.json`. 

We started with issues and cleaned the issues tag to separate the comma-separated issues to list for multi-label classification. As there is a large class imbalance in the issues, we selected only issues that occur in the top 90 percentile frequently assigned issues as our target labels. We selected this based on the assumption that infrequent issues are rare and requires special care to be assigned correctly. This narrowed down our cases list to ~9K.

Using the API response for each case, we identified the docket(s) associated with each case and further narrowed down our list to only cases with one docket for the exploratory phase. Cases with multiple dockets require special care to identify the main docket and cases that do not have dockets lack sufficient information to accurately assign issues. This narrowed down our cases list to ~6.5K.

We noted that the document that has the best information for issue assignment is the complaint, however, the complaints are stored within the Clearinghouse S3 buckets as PDFs, thus requiring OCR to extract the texts from the pdf in order to be used as features. Since Free Law already parses PDFs and saves them as plain text formats in our database, we considered looking for the dockets from RECAP, however, we noted that not all dockets in Clearinghouse are available in RECAP, and not all dockets in Clearinghouse are associated with a unique RECAP link. This may be due to lack of data in RECAP or data error in Clearingouse which we plan to explore further in the next phase.

In the current phase, given the difficulties in obtaining the complaints in plain texts format, we chose to use the docket entries progression as a proxy for a baseline. We extracted the docket entries from Clearinghouse API response and sorted them by the filing date, and chained the docket entries using natural language to create a text description of the docket entries progression. We note that docket entries do NOT have the neccessary details required to accurately identify the issues, and this is simply to experiment with the model setup.

We split the data to train and test set with a 70-30 split, inspected the distribution of the data (state, court, case status, etc) to ensure the test split is representative of the population.

## Modeling
We began with a pretrained ModernBERT model and ran inference on the test set using `ModernBertForSequenceClassification`. We do not expect the model to produce promising results as the model was not finetuned to perform the classification task on our data and docket entries progression is not the best feature for this task. This is simply to establish a baseline for all future experiments. The baseline results can be found at `4a.modernbert_inference.ipynb`.

We then utilized the training set to finetune the ModernBERT multi-label classification on our data. Due to the memory limitation, I truncated the feature texts to 512 tokens. This is simply to build out the training pipeline to ensure a model is trainable. To train the model on the entire docket progression, and/or docket documents including complaints, we can use the same script, by simply changing the feature to a new feature. The script can be found at `4b.modernbert_train.ipynb`.

To ensure completeness, I used the trained model to run inference to confirm the training script was functioning as intended. Note that the results are clearly overfitted to two classes due to the class imbalance, the text truncation, and the lack of important features (complaint). The script can be reused once a properly model training was done `4c.trained_inference.ipynb`.

## Next Steps
1. Labels:
   - One concern is that the data from Clearinghouse is focused on civil rights, as are the issues and causes of actions. However, the data in RECAP spans many other legal domains. A model trained to predict Clearinghouse labels will not be able to generaize to the data in RECAP, as the labels do not represent the population, and the model is overfitted to a specific legal domain.
   - To combat this issue, we need to identify a list of issues that are generalizable to the overall RECAP population.
2. Features:
   - As mentioned above, the most predictive feature for this task are the actual document contents (eg. Complaint), the documents in Clearinghouse are in PDF format and requires parsing to be usable in a classification model.
   - Free Law uses [Doctor](https://free.law/projects/doctor) to parse PDFs, we can utilize this tool to parse the PDFs to plain texts.
3. Multiple dockets for one case:
   - Sometimes, one case can have many dockets, currently, these dockets are properly associated with case in Clearinghouse, however, each docket are treated as standalone objects in RECAP. We currently do not have the capacity to link all dockets to cases in RECAP, due to the sheer volume of dockets.
   - To accommodate for the data design in RECAP, I proposed we predict the metadata associated with each docket (instead of with each case). For cases that have more than one dockets in Clearinghouse, the docket level predictions can help inform human reviewers on the appropriate tag for the case.
4. Missing FLP links:
   - Due to different data sources between Clearinghouse and FLP, not all cases in Clearinghouse are linked to or available in RECAP.
   - We should perform a more detailed analysis on the cases/dockets missing in RECAP and consider ways to consolidate/reconcile.
5. Multiple FLP links:
   - Sometimes, one case can have multiple FLP links to different dockets.
   - We should perform a more detailed analysis and reconcile the variances.
6. Modeling:
   - Once we have the features and the data, we should utilize the scripts above to train the model. In addition to the multi-label classification head already available through the Transformer models, we can also consider extracting the embeddings from the Transformer model (Last Hidden State) and other classification models, such as `OneVsRestClassifier` from sklearn with `LogisticRegression`, `RandomForestClassifier`, `Support Vector Machine`, or boosted trees such as `XGBoost`.

NOTE:
Due to large size files exceeding GitHub limit, the data files are stored in the [GDrive](https://drive.google.com/drive/folders/1mkEZf7ni0JNAyLwBO1IbfX57W-caAsJX?usp=drive_link).

## Status as of May 14, 2025

All experiments are housed in experiments_506 folder, unfortunately the data with the document texts are too large for GitHub, so the data are not in this repo. See https://drive.google.com/drive/folders/1hxKvd1uQxFNAIpvp1qjM64Po5K-SpzDk?usp=drive_link for the complete experiments and files.

Jasmine from the clearinghouse helped query their internal database to get the issue categories for each case and to get the document texts for each documents. I cleaned and preprocessed the datasets to create train, val, and test datasets and performed EDA on the data.

For each case, the content represents a natural language description of the case metadata (court, state, and case name) as well as the document texts for complaints and/or opinions, concatenated to a large string together with the document type. 

The contents are then chunked to context window sized chunks to be used for generating embedding using an encoder model. For this experiments, we used ModernBERT Base with 8192 context window as our baseline. The model was chosen for its superior performance, speed, and large context window compared to its predecessors. This approach is model agnostic and can be used with any encoder model.

Since each case can have one or more issue categories (labels) with substantial class imbalance among the categories, I chose to create binary classifications for each issue category. Each model will be trained on a balanced dataset of binary labels (1 for if the case has the target issue category, 0 for if the case does not) with the content embeddings as the feature. The models experimented are logistic regression, random forest, support vector machine, and gradient boosted tree models. To ensure consistency among all models, I selected the same number of training samples for all categories (240 cases, half positive, half negative). This number was chosen as a down-sampling technique to accomodate the less common classes, such as COVID. The data is carefully split such that the same case do not appear in both train, val, and test sets, to avoid any potential leakage. When training the binary classification models, I split the training data to train/val split so I can hypertune the models. Once the best model has been identified, I run the evaluation on the validation set to identify additional potential areas for improvements, such as the hyperparameters and the classification threshold. The models are then retrained with the entire training set (without validation split) to produce the final classification models to be used for final evaluation on the test set and for production.

To generate the embeddings, the chunked contexts are provided to the model as inputs with shape [, 8192] where 8192 is the context window (ie, number of tokens). The last hidden state generated by the model is the embedding for each batch of chunks with shape [batch_size, 8192, 786] where 8192 is the context window, 786 is the embedding dimension, and the batch size is determined by the GPU memory limitation not exceeding the number of chunks for each case. For most cases, we used either 16 or 8 as the batch size. We then take the mean first along the context window dimension to reduce the embedding dimension to [batch_size, 786] such that each batch is represented by one embedding of dimension 786, we then take the mean along the batch dimension to reduce the embedding dimension to [, 786] such that each case (regardless of the number of chunks / lengths of content), is represented by one embedding of dimension 786. To the extent a case is represented by numerous batches, we follow a similar approach to reduce the dimensions, except instead of taking the mean along the batch dimension, we first concatenate the batch level dimensions of size [batch_size, 786] to produce chunk level dimensions of size [num_chunks, 786] and then take the mean along the chunk dimension to reduce the embedding to [, 768].

All notebooks in the 8-series are for generating the embeddings for validation, test, and each issue category of training data. 

The embeddings are generated on a T4 GPU instance in Google Colab, the resulting embeddings are saved in the respective embeddings folders under the data folder. This approach (using embeddings as features) allows us to experiment different classification algorithms on CPU instances without the need for continuous GPU instances. This also allows us the flexibility of adding additional classes and easily training classification models for the additional classes using CPU instances only. These embeddings can also be used for other tasks such as clustering similar cases together.

Each notebook in the 9-series outlines the steps I undertook to train each binary classification model using the case content embeddings. The logistic regression models were trained through manual tuning by observing the training curves, the other models were trained through grid search to find the best hyperparameters based on validation performance.

All the trained models are saved in the models folder for future inference. Different issue categories may use different algorithms, depending on the validation performance.

For this round of experiments, I only selected 5 issue categories to gain a baseline expectation of the task and gauge the overall feasibility of the approach. Results can be seen from 10.val_eval.ipynb file. The results are decent but obviously more work is need to experiment with the embedding generation model, finetuning the embedding generation model, hypertuning the classification models, and test the performance on the overall sets.

For future experiments:
1. Try different embedding generation models such as the alea models which are specifically focused on legal domain, or other encoder models finetuned with legal context. We can also look into finetuning our own encoder model with our corpus to enhance the legal understanding of these models.
2. Consider generating embeddings on document level and docket entry level such that we have one embedding for each document and one embedding for each docket entry. These embeddings can then be aggregated to case level to generate case level embeddings, and they can be used for semantic search & clustering (for similar case search) so we can accomplish multiple tasks with these embeddings.
3. This exploratory phase only selected 5 issue categories, we need to expand to all issue categories and also expand to issues in future experiments.
4. I only experimented with logistic regression, random forest, support vector machine, and XGBoost classification models, but there are other classification models that we can also look into. Also, these models need to be more carefully tuned to find the best fit. Once we identify the models and the best hyperparameters, we should train the models one last time with all the data so we can get the best performance before using for production. 
5. Since the design is for the models to take a first pass, and then a human expert to take a second pass, we can integrate statistics gathering to continuously evaluate the model performance against human review, and retrain the models as needed.
6. The binary classification design will allow us to add or remove classes easily. But of course, each model needs to be trained and tuned separately as well. The cpu-based models allow us to deploy and use these models at scale without the need for continuous GPU usage & with easy retrain.
7. Also, note that this dataset is based on clearinghouse data, which focuses on civil rights related cases, we need to expand this to add'l isssue categories and issues that can be more broadly applied to other civil cases as well as criminal and bankruptcy cases.