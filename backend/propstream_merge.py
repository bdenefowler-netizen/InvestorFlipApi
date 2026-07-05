import pandas as pd
from io import BytesIO

MATCH_COLUMNS = [
    "Owner 1 First Name",
    "Owner 1 Last Name",
    "Mailing Address",
    "Mailing City",
    "Mailing State",
    "Mailing Zip",
]

def merge_propstream_files(marketing_file, contacts_file):
    df_list = pd.read_excel(marketing_file)
    df_contacts = pd.read_csv(contacts_file)

    df_contacts = df_contacts.drop(
        columns=["Street Address", "City", "State", "Zip"],
        errors="ignore"
    )

    df_contacts = df_contacts.rename(columns={
        "First Name": "Owner 1 First Name",
        "Last Name": "Owner 1 Last Name",
        "Mail Street Address": "Mailing Address",
        "Mail City": "Mailing City",
        "Mail State": "Mailing State",
        "Mail Zip": "Mailing Zip",
    })

    merged = pd.merge(
        df_list,
        df_contacts,
        on=MATCH_COLUMNS,
        how="left"
    )

    return merged

def dataframe_to_csv_response(df):
    output = BytesIO()
    df.to_csv(output, index=False)
    output.seek(0)
    return output