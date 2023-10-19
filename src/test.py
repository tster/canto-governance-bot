import requests

link = 'https://rest.cosmos.directory/canto/cosmos/gov/v1beta1/proposals'
response = requests.get(link, headers={
            'accept': 'application/json'}) # 2 = voting period
        # print(response.url)
print(response.json()['proposals'])

props = response.json()['proposals']
