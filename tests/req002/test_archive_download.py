"""Archive downloads retain their names across HTTP header encoding."""
from email.message import Message
import io
import zipfile

import pytest


def download_name(response):
    header = Message()
    header["Content-Disposition"] = response.headers["content-disposition"]
    assert header.get_content_disposition() == "attachment"
    return header.get_filename()


@pytest.mark.parametrize("name", ["report.txt", 'report "draft".txt', "图表📊.txt"])
def test_archive_download_filename_roundtrips_through_header(api, name):
    content = "archive filename fixture\n".encode()
    source = api.homes["alice"] / name
    source.write_bytes(content)
    response = api.request("POST", "/files/archive", json={"paths": [str(source)]})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/zip")
    assert download_name(response) == name + ".zip"
    if name == "report.txt":
        assert response.headers["content-disposition"] == 'attachment; filename="report.txt.zip"'
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.namelist() == [name]
        assert archive.read(name) == content


def test_multiple_unicode_inputs_keep_download_name_and_zip_entries(api):
    names = ["说明.txt", "图表📊.txt"]
    paths = []
    for name in names:
        source = api.homes["alice"] / name
        source.write_text(name, encoding="utf-8")
        paths.append(str(source))
    response = api.request("POST", "/files/archive", json={"paths": paths})
    assert response.status_code == 200, response.text
    assert download_name(response) == "download.zip"
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.namelist() == names
        for name in names:
            assert archive.read(name).decode("utf-8") == name
