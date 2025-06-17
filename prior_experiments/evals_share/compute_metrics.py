import pandas as pd
import sys

filepath = sys.argv[1]

responses = pd.read_csv(filepath, sep='\t')

num_responses = len(responses)
truth_positives = 0
true_positives = 0
false_positives = 0
truth_negatives = 0
true_negatives = 0
false_negatives = 0
model_errors = 0

# Counting
for idx, row in responses.iterrows():
    truth = row['overruled']
    prediction = row['prediction']
    if truth == 'yes':
        truth_positives += 1
        if prediction == 'yes':
            true_positives += 1
        else:
            false_negatives += 1
    if truth == 'no':
        truth_negatives += 1
        if prediction == 'yes':
            false_positives += 1
        else:
            true_negatives += 1


accuracy = (true_positives + true_negatives) / num_responses
precision = true_positives / (true_positives + false_positives)
recall = true_positives / truth_positives
f_score = 2 * (precision * recall) / (precision + recall)
specificity = true_negatives / truth_negatives
print('METRICS')
print(f'Accuracy: {accuracy * 100:.2f}%   (% correct)')
print(f'Precision: {precision * 100:.2f}%   (true positives / predicted as positive)')
print(f'Recall: {recall * 100:.2f}%   (true positives / ground truth positives)')
print(f'F-score: {f_score * 100:.2f}%   (~avg precision-recall)')
print(f'Specificity: {specificity * 100:.2f}%   (true negatives / ground truth negatives)')
print('---------')
print('DATASET')
print('Total Responses:', num_responses)
print('Total Positives:', truth_positives)
print('Total Negatives:', truth_negatives)
print('---------')
print('RECOUNT')
print('True positives:', true_positives)
print('True negatives:', true_negatives)
print('False positives:', false_positives)
print('False negatives:', false_negatives)
