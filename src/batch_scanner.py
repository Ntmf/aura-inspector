import csv
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from colored_logger import logger
from aura_helper import AuraError


def load_ignore_list(path):
	"""Load an ignore list file. One object name per line, # for comments."""
	ignore_set = set()
	with open(path, 'r') as f:
		for line in f:
			line = line.strip()
			if line and not line.startswith('#'):
				ignore_set.add(line.lower())
	return ignore_set


def load_org_configs(csv_path):
	"""Load org configurations from a CSV file."""
	configs = []
	with open(csv_path, 'r', newline='') as f:
		reader = csv.DictReader(f)
		for row_num, row in enumerate(reader, start=2):
			url = row.get('url', '').strip()
			if not url:
				logger.warning(f'Skipping row {row_num}: no URL provided')
				continue
			if url.endswith('/'):
				url = url[:-1]

			config = {
				'url': url,
				'cookies': row.get('cookies', '').strip() or None,
				'app': row.get('app', '').strip() or None,
				'aura': row.get('aura', '').strip() or None,
				'context': row.get('context', '').strip() or None,
				'token': row.get('token', '').strip() or None,
				'no_gql': row.get('no_gql', '').strip().lower() == 'true',
			}

			if config['app'] and config['app'] == "/":
				config['app'] = "/s"

			configs.append(config)

	return configs


def sanitize_hostname(url):
	"""Create a filesystem-safe directory name from a URL."""
	# Remove protocol
	name = re.sub(r'^https?://', '', url)
	# Replace non-alphanumeric chars with underscores
	name = re.sub(r'[^a-zA-Z0-9._-]', '_', name)
	return name


def scan_org(org_config, common_args, ignore_list, output_dir):
	"""Scan a single org, catching errors so the batch continues."""
	from aura_cli import audit, save_results, print_summary

	url = org_config['url']
	try:
		result = audit(
			url=url,
			cookies=org_config['cookies'],
			object_list=common_args.get('object_list'),
			proxy=common_args.get('proxy'),
			insecure=common_args.get('insecure', False),
			app=org_config['app'],
			aura_path=org_config['aura'],
			context=org_config['context'],
			token=org_config['token'],
			no_gql=org_config['no_gql'] or common_args.get('no_gql', False),
			ignore_list=ignore_list,
		)

		# Save per-org output
		if output_dir:
			org_dir = os.path.join(output_dir, sanitize_hostname(url))
			save_results(result, org_dir)

		print_summary(result)
		return result

	except AuraError as e:
		logger.error(f'[{url}] Scan failed: {e}')
		return {"url": url, "error": str(e)}
	except Exception as e:
		logger.error(f'[{url}] Unexpected error: {e}')
		return {"url": url, "error": str(e)}


def build_consolidated_report(results, ignore_list):
	"""Aggregate scan results across all orgs."""
	object_map = {}  # object_name -> list of {url, record_count, gql_count}

	for result in results:
		if "error" in result:
			continue

		url = result["url"]

		# Process standard records
		for obj_name, data in result.get("records", {}).items():
			if ignore_list and obj_name.lower() in ignore_list:
				continue
			total_count = data.get('total_count', 0)
			if total_count == 0:
				continue
			if obj_name not in object_map:
				object_map[obj_name] = []
			# Check if we already have an entry for this URL (avoid duplicates)
			existing_urls = {entry['url'] for entry in object_map[obj_name]}
			if url not in existing_urls:
				object_map[obj_name].append({
					'url': url,
					'record_count': total_count,
					'gql_count': 0,
				})

		# Merge GraphQL counts
		for obj_name, data in result.get("gql_records", {}).items():
			if ignore_list and obj_name.lower() in ignore_list:
				continue
			gql_count = data.get('total_count', 0)
			if gql_count == 0:
				continue
			if obj_name not in object_map:
				object_map[obj_name] = []
			# Update existing entry or add new one
			existing = next((e for e in object_map[obj_name] if e['url'] == url), None)
			if existing:
				existing['gql_count'] = gql_count
			else:
				object_map[obj_name].append({
					'url': url,
					'record_count': 0,
					'gql_count': gql_count,
				})

	return object_map


def write_consolidated_csv(report, output_path):
	"""Write consolidated report as CSV."""
	os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)

	# Sort by number of orgs exposed (descending)
	sorted_objects = sorted(report.items(), key=lambda x: len(x[1]), reverse=True)

	with open(output_path, 'w', newline='') as f:
		writer = csv.writer(f)
		writer.writerow(['object_name', 'total_org_count', 'org_urls'])
		for obj_name, orgs in sorted_objects:
			org_urls = ';'.join(entry['url'] for entry in orgs)
			writer.writerow([obj_name, len(orgs), org_urls])

	logger.info(f'Consolidated CSV report written to {output_path}')


def write_consolidated_json(report, scan_metadata, output_path):
	"""Write consolidated report as JSON with full detail."""
	os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)

	output = {
		"scan_date": scan_metadata["scan_date"],
		"total_orgs_scanned": scan_metadata["total_orgs_scanned"],
		"total_orgs_succeeded": scan_metadata["total_orgs_succeeded"],
		"total_orgs_failed": scan_metadata["total_orgs_failed"],
		"failed_orgs": scan_metadata["failed_orgs"],
		"ignored_objects": scan_metadata.get("ignored_objects", []),
		"exposed_objects": {},
	}

	# Sort by number of orgs exposed (descending)
	sorted_objects = sorted(report.items(), key=lambda x: len(x[1]), reverse=True)

	for obj_name, orgs in sorted_objects:
		output["exposed_objects"][obj_name] = {
			"org_count": len(orgs),
			"orgs": orgs,
		}

	with open(output_path, 'w') as f:
		json.dump(output, f, indent=2)

	logger.info(f'Consolidated JSON report written to {output_path}')


def run_batch(batch_file, ignore_list, output_dir, proxy, insecure, object_list, no_gql, workers=1):
	"""Run batch scanning across multiple orgs."""
	configs = load_org_configs(batch_file)
	if not configs:
		logger.error('No valid org configurations found in batch file')
		return

	logger.info(f'Loaded {len(configs)} org configurations from {batch_file}')

	common_args = {
		'proxy': proxy,
		'insecure': insecure,
		'object_list': object_list,
		'no_gql': no_gql,
	}

	results = []
	failed_orgs = []

	if workers > 1:
		logger.info(f'Running with {workers} parallel workers')
		completed_count = 0
		# Map futures back to their config index for ordering
		with ThreadPoolExecutor(max_workers=workers) as executor:
			future_to_config = {
				executor.submit(scan_org, config, common_args, ignore_list, output_dir): config
				for config in configs
			}
			for future in as_completed(future_to_config):
				config = future_to_config[future]
				completed_count += 1
				try:
					result = future.result()
				except Exception as e:
					result = {"url": config["url"], "error": str(e)}
				logger.info(f'Completed {completed_count}/{len(configs)}: {config["url"]}')
				results.append(result)
				if "error" in result:
					failed_orgs.append({"url": result["url"], "error": result["error"]})
	else:
		for i, config in enumerate(configs, start=1):
			logger.info(f'--- Scanning org {i}/{len(configs)}: {config["url"]} ---')
			result = scan_org(config, common_args, ignore_list, output_dir)
			results.append(result)

			if "error" in result:
				failed_orgs.append({"url": result["url"], "error": result["error"]})

	# Build consolidated report
	succeeded = len(results) - len(failed_orgs)
	logger.info(f'Batch scan complete: {succeeded}/{len(configs)} orgs succeeded, {len(failed_orgs)} failed')

	report = build_consolidated_report(results, ignore_list)

	scan_metadata = {
		"scan_date": str(date.today()),
		"total_orgs_scanned": len(configs),
		"total_orgs_succeeded": succeeded,
		"total_orgs_failed": len(failed_orgs),
		"failed_orgs": failed_orgs,
		"ignored_objects": sorted(ignore_list) if ignore_list else [],
	}

	# Write consolidated reports
	os.makedirs(output_dir, exist_ok=True)
	csv_path = os.path.join(output_dir, 'consolidated_report.csv')
	json_path = os.path.join(output_dir, 'consolidated_report.json')

	write_consolidated_csv(report, csv_path)
	write_consolidated_json(report, scan_metadata, json_path)

	# Print summary
	print('')
	print('=== Consolidated Summary ===')
	print(f'Orgs scanned: {len(configs)}')
	print(f'Orgs succeeded: {succeeded}')
	print(f'Orgs failed: {len(failed_orgs)}')
	print(f'Unique exposed objects: {len(report)}')
	print('')
	if report:
		# Print top exposed objects
		sorted_objects = sorted(report.items(), key=lambda x: len(x[1]), reverse=True)
		print(f'{"Object Name":<40} {"Orgs Exposed":<15}')
		print('-' * 55)
		for obj_name, orgs in sorted_objects[:20]:
			print(f'{obj_name:<40} {len(orgs):<15}')
		if len(sorted_objects) > 20:
			print(f'... and {len(sorted_objects) - 20} more objects (see consolidated report)')
	print('')
	if failed_orgs:
		print('Failed orgs:')
		for org in failed_orgs:
			print(f'  {org["url"]}: {org["error"]}')
		print('')

	logger.info(f'Results saved to {output_dir}')
