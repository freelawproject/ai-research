def show_metrics(cm, y_true):
    total = len(y_true)
    tn, fp, fn, tp = cm.ravel()
    ap = y_true.sum()
    an = total - ap

    accuracy = (tp + tn) / len(y_true)
    precision = tp / (tp + fp)
    recall = tp / ap
    f_score = 2 * (precision * recall) / (precision + recall)
    specificity = tn / an

    print("precision: of all predicted positives, how many were actual positives")
    print("recall: of all actual positives, how many we predicted to be positives")
    print('---------')
    print('METRICS')
    print(f'Accuracy: {accuracy:.2f}')
    print(f'Precision: {precision:.2f}')
    print(f'Recall: {recall:.2f}')
    print(f'F-score: {f_score:.2f}')
    print(f'Specificity: {specificity:.2f}')
    print('---------')
    print('DATASET')
    print('Total Responses:', total)
    print('Total Positives:', ap)
    print('Total Negatives:', an)
    print('---------')
    print('RECOUNT')
    print('True positives:', tp)
    print('True negatives:', tn)
    print('False positives:', fp)
    print('False negatives:', fn)