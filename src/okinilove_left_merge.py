#%%
from bs4 import BeautifulSoup
import pandas as pd
url = "../docs/okinilove_reconstructed.html"
with open(url, "r", encoding="utf-8") as f:
    html = f.read()
soup = BeautifulSoup(html, "html.parser")

# %%
reports = []
soup = soup.find("div",class_="card-container")
for card in soup.find_all("div",class_="card"):
    shop_name = card.find("h3").get_text()
    shop_name = shop_name.split("-")[0].strip()
    name_tag = card.find("a")
    girl_name = name_tag.get_text(strip=True) if name_tag else None
    girl_url = name_tag["href"] if name_tag else None

    detail = card.find_all("details")[0]

    summary = detail.find("summary")

    report_link_tag = summary.find("a")
    report_link = report_link_tag["href"] if report_link_tag else None
    eval = summary.get_text(strip=True)

    report_text = detail.find("pre").get_text(strip=True)


    reports.append({
        "shop_name" :shop_name,
        "girl_name": girl_name,
        "girl_url":girl_url,
        "report_url": report_link,
        "evaluations": eval,
        "report_text":report_text
    })

df3 = pd.DataFrame(reports)
# print(df3[["girl_name","shop_name"]])
df3.loc[df3["girl_url"].str.contains("cityheaven.net", na=False), "cityheaven_url"] = df3["girl_url"]
# %%
