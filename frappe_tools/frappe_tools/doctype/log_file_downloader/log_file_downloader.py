# Copyright (c) 2025, sakthi123msd@gmail.com and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
import os
from os import listdir
from zipfile import ZipFile
import shutil
from frappe.utils.file_manager import save_file


class LogFileDownloader(Document):
	pass


@frappe.whitelist()
def get_logs_namspaces():
	files_list = listdir(path='../logs/')
	log_file_result_dict = {}
	for i in files_list:
		if not '.log' in i:
			continue
		fname = i.split('.log')[0]
		if fname not in log_file_result_dict:
			log_file_result_dict[fname] = 1
		else :
			log_file_result_dict[fname] += 1

	return log_file_result_dict

@frappe.whitelist()
def download_log_zips(file_name):
	files_list = listdir(path='../logs/')
	if os.path.exists(f'../logs/{file_name}'):
		shutil.rmtree(f'../logs/{file_name}')
	os.mkdir(f'../logs/{file_name}')
	base_path = '../logs/'
	target_path = f'../logs/{file_name}/'
	for i in files_list:
		if not '.log' in i:
			continue
		fname = i.split('.log')[0]
		if fname == file_name:
			shutil.copy2(base_path+i, target_path)
	zip_path = shutil.make_archive(file_name, 'zip', base_path, file_name)

	with open(zip_path, "rb") as f:
		file_data = f.read()
	frappe.local.response.filename = f"{file_name}.zip"
	frappe.local.response.filecontent = file_data
	frappe.local.response.type = "binary"
	os.remove(zip_path)
	shutil.rmtree(target_path)
			
