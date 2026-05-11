import json
import pandas as pd
import numpy as np

with open("C:/Users/Papa Offei/Documents/Machine Learning/mehuwo/curriculum/fraction_diagnostic_item_bank_v1.json","r") as file:
    test = json.load(file)

IDs = []
Q_Vectors = []

for cat in ["single_skill_items",'multi_skill_items']:
    for item in test[cat]:
        IDs.append(item['item_id'])
        Q_Vectors.append(item['q_vector'])
   
nodes = test['meta']['node_order']


Q_Vectors = np.array(Q_Vectors).transpose().tolist()

df = {}

df['IDS'] = IDs
for node,points in zip(nodes,Q_Vectors):
    df[node] = points



df = pd.DataFrame(df)
print(df.head())
