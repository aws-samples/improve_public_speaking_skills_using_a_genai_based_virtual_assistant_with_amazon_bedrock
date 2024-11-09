# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

import streamlit as st
import uuid
import json
import time

import utils.stepfn as stepfn
from utils.auth import Auth
from utils.config_file import Config


st.set_page_config(layout="wide")

st.title("Public Speaking Mentor AI Assistant")

# ID of Secrets Manager containing cognito parameters
secrets_manager_id = Config.SECRETS_MANAGER_ID

# Initialise CognitoAuthenticator
authenticator = Auth.get_authenticator(secrets_manager_id)

# Authenticate user, and stop here if not logged in
is_logged_in = authenticator.login()
if not is_logged_in:
    st.stop()


def logout():
    if "psmb_exeuction_arn" in st.session_state:
        del st.session_state["psmb_exeuction_arn"]
    display_no_state_machine_status()
    authenticator.logout()

with st.sidebar:
    st.text(f"Welcome,\n{authenticator.get_username()}")
    st.button("Logout", "logout_btn", on_click=logout)


execution_status_container = None
sfn_name = stepfn.get_sfn_name()


# Populate a unique user ID to use for naming the Step Functions execution
if "user_id" not in st.session_state:
    st.session_state.user_id = str(uuid.uuid4())

def display_state_machine_status(status_markdown):
    if execution_status_container:
        execution_status_container.empty()
        with execution_status_container.container():
            st.subheader("⚙️ Recommendation Generation Status")
            st.markdown(status_markdown)


def display_no_state_machine_status():
    if execution_status_container:
        execution_status_container.empty()
        with execution_status_container.container():
            st.subheader("⚙️ Recommendation Generation Status")
            st.write("Not started yet.")


# def execute_state_machine(novel):
#     input = {"novel": novel}
#     execution_arn = stepfn.start_execution(
#         "MyStateMachine-ir627wdfw",
#         st.session_state.user_id,
#         json.dumps(input),
#     )
#     st.session_state.psmb_exeuction_arn = execution_arn
#     return stepfn.poll_for_execution_completion(
#         execution_arn, display_state_machine_status
#     )

def get_state_machine_status():
    execution_arn = stepfn.get_running_execution_arn(sfn_name)
    print(f"execution_arn: {execution_arn}")
    st.session_state.psmb_exeuction_arn = execution_arn
    return stepfn.poll_for_execution_completion(
        execution_arn, display_state_machine_status
    )


demo_col, behind_the_scenes_col = st.columns(spec=[1, 1], gap="large")

with behind_the_scenes_col:
    execution_status_container = st.empty()

    if "psmb_exeuction_arn" in st.session_state:
        status_markdown = stepfn.describe_execution(
            st.session_state.psmb_exeuction_arn
        )
        display_state_machine_status(status_markdown)
    else:
        display_no_state_machine_status()

    st.subheader("🔍 Step Functions state machine")
    

with demo_col:
    
    st.info(
        "Please upload your Audio or Video files to generate recommendations about your speech delivery."
    )
    # File uploader
    #uploaded_file = st.file_uploader("Choose a file", type=["audio/*", "video/*"], accept_multiple_files=False)
    uploaded_file = st.file_uploader("Choose an Audio or Video file", accept_multiple_files=False)

    # Check if a file is uploaded
    if uploaded_file is not None:
        # File type validation
        if uploaded_file.type.startswith("audio/") or uploaded_file.type.startswith("video/"):
            # File size validation
            if uploaded_file.size <= 200 * 1024 * 1024:  # 200MB limit
                # Submit button
                submitted = st.button("Upload File")
                if submitted:
                    # Display spinner
                    with st.spinner("Uploading file..."):
                        # Call function to upload file to S3
                        stepfn.upload_to_s3(uploaded_file)

                    # Display result
                    st.success(f"File '{uploaded_file.name}' uploaded successfully!")

                    # Start polling Step Function status
                    with st.spinner("Wait for it..."):
                        if "psmb_exeuction_arn" in st.session_state:
                            del st.session_state["psmb_exeuction_arn"]
                        display_no_state_machine_status()

                        time.sleep(2)   # nosemgrep  
                        response = get_state_machine_status()

                        st.session_state.psmb_exeuction_status = response["status"]
                        if response["status"] == "SUCCEEDED":
                            output = json.loads(response["output"])
                            st.session_state.psmb_content = output

                    if st.session_state.psmb_exeuction_status == "SUCCEEDED":
                        st.success("Done!")
                        st.subheader("🚀 Speech Recommendations")
                        st.write(st.session_state.psmb_content)
                    else:
                        st.error("The speech recommendations could not be generated. Please try again.")
            else:
                st.error("File size exceeds the 10MB limit.")
        else:
            st.error("Invalid file type. Only audio and video files are allowed.")
    else:
        st.info("Waiting for file upload...")
