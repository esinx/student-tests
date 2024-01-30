import json
import subprocess
import requests
import shlex
import time, os, sys
import re


SERVER_IP = os.getenv('SERVER_IP') # EC2 instance IP of database/server
SERVER_PORT = os.getenv('SERVER_PORT', '3000')
SERVER_URI = f"http://{SERVER_IP}:{SERVER_PORT}"
AUTH_TOKEN = os.getenv('AUTH_TOKEN')


def load_config():
  global num_public_tests_for_access
  with open('config.json', 'r') as file:
    config = json.load(file)
    num_public_tests_for_access = config['numPublicTestsForAccess']


def run_curl_command(curl_command):
  modified_curl_command = curl_command + ' -w "\\n%{http_code}"'
  args = shlex.split(modified_curl_command)

  result = subprocess.run(args, capture_output=True, text=True)

  output_parts = result.stdout.strip().split('\n')
  response_body = '\n'.join(output_parts[:-1])
  response_code = int(output_parts[-1])

  return result.returncode, result.stdout, result.stderr, response_code, response_body

def run_curl_test(test):
  curl_command = test['test']['command']
  response_type = test['test']['response-type']
  expected_status = test['test']['response']['status']

  returncode, _, stderr, response_code, response_body = run_curl_command(curl_command)

  if returncode != 0:
    return {"success": False, "reason": f"Error executing test '{test['name']}':\n{stderr}"}

  if response_code != expected_status:
    return {"success": False, "reason": f"Test '{test['name']}' failed: Expected status {expected_status}, got {response_code}"}
  
  if response_type == "json":
    try:
      response_json = json.loads(response_body)
    except json.JSONDecodeError:
      return {"success": False, "reason": f"Test '{test['name']}' failed: Response body is not valid JSON"}
    expected_json = test['test']['response']['json']
    if response_json != expected_json:
      return {"success": False, "reason": f"Test '{test['name']}' failed: Expected body {expected_body}, got {response_json}"}
  elif response_type == "text":
    expected_body = test['test']['response']['body']
    if response_body != expected_body:
      return {"success": False, "reason": f"Test '{test['name']}' failed: Expected body {expected_body}, got {response_body}"}

  return {"success": True, "reason": f"Test '{test['name']}' Passed"}


def run_test(test):
  if test["type"] == "curl":
    return run_curl_test(test)
  else:
    return {"success": False, "reason": f"Unknown test type '{test['type']}'"}


def run_tests(tests):
  results = {"passed": 0, "failed": 0, "total": len(tests), "results": []}

  for test in tests:
    test_result = run_test(test)
    results["results"].append({
        "name": test["name"],
        "result": test_result,
        "test": test
    })
    if test_result["success"]:
      results["passed"] += 1
    else:
      results["failed"] += 1
      
  return results


def check_database_health():
  url = f"{SERVER_URI}/"
  headers = {'Authorization': AUTH_TOKEN}
  try:
    response = requests.get(url, headers=headers)
    return response.status_code == 200
  except requests.RequestException:
    return False


def get_student_id():
  with open('/autograder/submission_metadata.json', 'r') as file:
    metadata = json.load(file)
    return metadata['users'][0]['id']


def get_assignment_title():
  with open('/autograder/submission_metadata.json', 'r') as file:
    metadata = json.load(file)
    title = metadata['assignment']['title']
    safe_title = re.sub(r'\s+', '_', title)
    safe_title = re.sub(r'[^\w-]', '', safe_title)
    return safe_title


def upload_tests(assignment_title, student_id, tests, params):
  url = f"{SERVER_URI}/submit-tests/{assignment_title}?student_id={student_id}"
  headers = {'Content-Type': 'application/json', 'Authorization': AUTH_TOKEN}
  response = requests.post(url, params=params, json=tests, headers=headers)
  return response


def upload_results(assignment_title, student_id, results):
  url = f"{SERVER_URI}/submit-results/{assignment_title}?student_id={student_id}"
  headers = {'Content-Type': 'application/json', 'Authorization': AUTH_TOKEN}
  response = requests.post(url, json=results, headers=headers)
  return response


def start_server(server_path, npm_install=False):
  if npm_install:
    subprocess.run(["npm", "install"], cwd=server_path)
  process = subprocess.Popen(["node", "index.js"], cwd=server_path)
  time.sleep(5)
  return process


def stop_server(process):
  process.terminate()
  process.wait()


def write_output(data):
  if len(data["tests"]) == 0:
    data["score"] = 0
  with open('/autograder/results/results.json', 'w') as file:
    json.dump(data, file)


def main():
  load_config()
  
  # Read tests
  try:
    with open('tests.json', 'r') as file:
      tests = json.load(file)
  except:
    tests = []
  
  output_str = ""
  if len(tests) > 0:
    # Run tests on sample server
    sample_server = start_server("/autograder/source/sample-server")
    sample_results = run_tests(tests)
    stop_server(sample_server)

    # Format feedback and ensure they passed sample
    feedback = [{
      "name": "SAMPLE SOLUTION RESULT: " + result["name"],
      "status": "failed" if not result["result"]["success"] else "passed",
      "score": 0 if not result["result"]["success"] else 0,
      "output": "Description: " + result["test"]["description"] + "\n\n" + result["result"]["reason"] if "description" in result["test"] and result["test"]["description"] else result["result"]["reason"],
      "visibility": "visible"
    } for result in sample_results["results"]]
    successful_tests = [result["test"] for result in sample_results["results"] if result["result"]["success"]]

    if sample_results["total"] != sample_results["passed"]:
      output_str += "Some test cases did not pass sample implementation. If you believe any of these to be a mistake, please contact the assignment administrators. Only test cases that pass this sample may be uploaded. You can find the outcomes of running your tests on THE SAMPLE SOLUTION below.\n"
    else:
      output_str += "All uploaded tests passed the sample implementation!\n"
  else:
    output_str += "No tests were uploaded. You must have submitted at least one working test at some point to be able to run other students' tests.\n"
    feedback = []
    successful_tests = []

  # Ensure database is running
  if not check_database_health():
    write_output({"output": "Server is not running or not healthy. Please contact the assignment administrators. In the meantime, here are the outcomes of running your tests on THE SAMPLE SOLUTION.\n" + output_str, "tests": feedback})
    return
  student_id = get_student_id()
  assignment_title = get_assignment_title()

  # Upload tests to the database, get response of all tests
  response = upload_tests(assignment_title, student_id, successful_tests, {"num_public_tests": num_public_tests_for_access})
  json_response = response.json()
  if response.status_code < 200 or response.status_code >= 300 or not json_response['success']:
    write_output({"output": "Error uploading tests to the database. Please contact the assignment administrators. In the meantime, here are the outcomes of running your tests on THE SAMPLE SOLUTION.\n" + output_str, "tests": feedback})
    return
  if len(json_response['failedToAdd']) > 0:
    output_str += "Failed to upload all tests to the database. Make sure test names are unique if you want them to be counted seperately! Please see the following reasons:\n\n"
    for failure in json_response['failedToAdd']:
      output_str += failure['name'] + ": \t" + failure['reason'] + "\n"
    output_str += "\n"
  elif len(successful_tests) > 0:
    output_str += "All tests successfully uploaded to the database!\n"
  all_tests = response.json()['tests']

  # Run tests on student submission
  student_server = start_server("/autograder/submission", npm_install=True)
  all_results = run_tests(all_tests)
  stop_server(student_server)
  
  # Format feedback and return results
  feedback += [{
    "name": result["name"],
    "status": "failed" if not result["result"]["success"] else "passed",
    "score": 0 if not result["result"]["success"] else 0,
    "output": "Description: " + result["test"]["description"] + "\n\n" + result["result"]["reason"] if "description" in result["test"] and result["test"]["description"] else result["result"]["reason"],
    "visibility": "visible"
  } for result in all_results["results"]]

  if all_results["total"] != all_results["passed"]:
    output_str += "\nNot all available test cases passed your implementation. Please see the following breakdown.\n"
  elif all_results["total"] == 0:
    output_str += "\nNo available tests to run on your implementation. You must have submitted at least one working test at some point to be able to run other students' tests.\n"
  else:
    output_str += "\nAll available test cases passed your implementation!\n"

  # Upload results to the database
  upload_response = upload_results(assignment_title, student_id, [{"name": result["name"], "passed": result["result"]["success"]} for result in all_results["results"]])
  if upload_response.status_code != 200:
    output_str += "\nError uploading results to the database. Please contact the assignment administrators. You can still see the results of the test cases below, but the updated statistics have not been uploaded.\n"
  
  write_output({"output": output_str, "tests": feedback})

def setup():
  load_config()
  
  # Read default tests
  try:
    with open('default-tests.json', 'r') as file:
      tests = json.load(file)
  except:
    tests = []
  
  output_str = ""
  if len(tests) > 0:
    # Run tests on sample server
    sample_server = start_server("/autograder/source/sample-server")
    sample_results = run_tests(tests)
    stop_server(sample_server)

    feedback = [{
      "name": "SAMPLE SOLUTION RESULT: " + result["name"],
      "status": "failed" if not result["result"]["success"] else "passed",
      "score": 0 if not result["result"]["success"] else 0,
      "output": "Description: " + result["test"]["description"] + "\n\n" + result["result"]["reason"] if "description" in result["test"] and result["test"]["description"] else result["result"]["reason"],
      "visibility": "visible"
    } for result in sample_results["results"]]
    successful_tests = [result["test"] for result in sample_results["results"] if result["result"]["success"]]

    if sample_results["total"] != sample_results["passed"]:
      output_str += "Some test cases did not pass sample implementation. Only test cases that pass this sample may be uploaded. You can find the outcomes of running your tests on THE SAMPLE SOLUTION below.\n"
    else:
      output_str += "All uploaded tests passed the sample implementation!\n"
  else:
    output_str += "No default tests were uploaded.\n"
    feedback = []
    successful_tests = []

  test_response = ""
  for test in feedback:
    test_response += test['name'] + ": " + test['status'] + "\n"
    test_response += test['output'] + "\n\n"

  # Ensure database is running
  if not check_database_health():
    print("Server is not running or not healthy. Please contact the database administrators. In the meantime, here are the outcomes of running your tests on THE SAMPLE SOLUTION.\n" + output_str + "\n" + test_response)
    return
  assignment_title = get_assignment_title()

  # Upload tests to the database, get response of all tests
  response = upload_tests(assignment_title, -1, successful_tests, {"num_public_tests": num_public_tests_for_access})
  json_response = response.json()
  if response.status_code < 200 or response.status_code >= 300 or not json_response['success']:
    print("Error uploading tests to the database. Please contact the database administrators. In the meantime, here are the outcomes of running your tests on THE SAMPLE SOLUTION.\n" + output_str + "\n" + test_response)
    return
  if len(json_response['failedToAdd']) > 0:
    output_str += "Failed to upload all tests to the database. Make sure test names are unique if you want them to be counted seperately! Please see the following reasons:\n\n"
    for failure in json_response['failedToAdd']:
      output_str += failure['name'] + ": \t" + failure['reason'] + "\n"
    output_str += "\n"
  elif len(successful_tests) > 0:
    output_str += "All tests successfully uploaded to the database!\n"

  print(output_str)

if __name__ == "__main__":
  if len(sys.argv) == 2:
    if sys.argv[1] == "--setup":
      setup()
    else:
      print("Invalid argument. Use --setup in autograder setup.")
  elif len(sys.argv) == 1:
    main()
  else:
    print("Invalid number of arguments. Use --setup in autograder setup.")
