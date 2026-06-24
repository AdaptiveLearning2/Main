import os
from supabase import create_client, Client #pip install supabase
from dotenv import load_dotenv   #pip install dotenv
from ollama import chat, generate
from ollama import ChatResponse
import json
from flask import Flask, jsonify,request
from flask_cors import CORS #pip install flask-cors
#python -m flask --app LLM_supabase_communication run

#CURRENTLY NOT IN USE IN APPLICATION

#Setup Flask app and enable CORS to communicate with React frontend
app = Flask(__name__)
CORS(app, origins=["http://localhost:5173"])

#Obtain accuracy data from frontend. 
load_dotenv() #need to create .env file in backend folder with SUPABASE_URL and SUPABASE_KEY
url = os.getenv("VITE_SUPABASE_URL")
key = os.getenv("VITE_SUPABASE_ANON_KEY")
supabase = create_client(url, key)  

 
@app.route("/api/performance", methods=["POST"])
def save_performance():
    data = request.json
    #print("Receieved from fronted: ", data)
    return jsonify({"status": "saved"})

@app.route("/api/performance", methods=["GET"])
def get_performance():
    user_id = request.args.get("user_id")

    response = supabase.table("user_math_performance") \
        .select("accuracy, stress, math_topics(topic_name)") \
        .eq("user_id", user_id) \
        .execute()
    
    return jsonify(response.data)

@app.route("/api/generate-question", methods=["GET"])
def generate_question():
    print("GENERATING QUESTION")
    user_id = request.args.get("user_id")

    #get performance data
    response = supabase.table("user_math_performance") \
        .select("accuracy, stress, math_topics(topic_name)") \
        .eq("user_id", user_id) \
        .execute()

    data = response.data 

    #filter out None default values 
    valid_accuracy = [x for x in data if x["accuracy"] is not None]
    valid_stress = [x for x in data if x["stress"] is not None]

    #might be useful to find weakest subjects
    if valid_accuracy:
        lowest_acc_search = min(valid_accuracy, key=lambda x: x["accuracy"])
        lowest_acc = lowest_acc_search["math_topics"]["topic_name"]
    else:
        lowest_acc = "None"
    if valid_stress:
        highest_stress_search = max(valid_stress, key=lambda x: x["stress"])
        highest_stress = highest_stress_search["math_topics"]["topic_name"]
    else:
        highest_stress = "None"


    #Works ok, gives incorrect answers fairly often though.
    
    prompt = f""" 
    Generate a math question for a 6-8th grade student. These students have special education needs and may be sensitive to stress. 
    Your goal is to provide a solvable math question that will be engaging without resulting in increased stress levels.
    Here is the student's data so far: {data}. Their lowest accuracy is in: {lowest_acc}. Their highest stress comes from: {highest_stress}

    Here is the list of the potential math-related topics: Ordering, Rationals, Operations, Expressions, Algebra, Geometry, Angle Relationships, Mean, Median, Mode, Probability 
    
    Return JSON with:
    - question_text
    - topic (selected from above list)
    - 4 answer options, one of which MUST BE the mathematically correct solution to the question. The other three responses must be incorrect. These should be listed under "answer_options" as an array.
      Each answer option should have a "value" field specifying the option and a "correct" field that is either true is the answer is correct and false otherwise.

    The JSON must follow this exact structure:
        {{
        "question_text": "Tom has 5 pencils in his pencil case. His friend gives him 2 more pencils. How many pencils does Tom have now?",
        "question_topic": "ordering",
        "answer_options": [
        {{"value": "3", "correct": false}}
        {{"value: "7", "correct": true}}
        {{"value: "10", "correct": false}}
        {{"value: "2", "correct": false}}
        }}

    There should only be one correct answer to each question. ENSURE that there is a mathematically correct answer option listed as correct.    

    RETURN ONLY JSON, with no additional characters or symbols before or after. 
    """

    #generate llm response
    response = generate(model="llama3.1:8b", 
                        prompt=prompt,
                        options = {"temperature": 0.7,
                                    "top_p": 100} #increase randomness, may need to adjust values 
                        )
    
    print(response.response)
    parsed = json.loads(response.response)
    return jsonify(parsed)


if __name__ == "__main__": #run flask app on port 5000, separate from React frontend which runs on 5173
    app.run(debug=True, port=5000)


