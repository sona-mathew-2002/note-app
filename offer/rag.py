
# rag.py
import os
import json
from PIL import Image
from langchain.vectorstores import Chroma
from langchain.chat_models import ChatOllama
from langchain.embeddings import FastEmbedEmbeddings
from langchain.schema.output_parser import StrOutputParser
from langchain.document_loaders import PyPDFLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.schema.runnable import RunnablePassthrough
from langchain.prompts import PromptTemplate
from langchain.vectorstores.utils import filter_complex_metadata
from langchain.schema import Document
from datetime import datetime
from openai import OpenAI
import base64
import io
from dateutil.relativedelta import relativedelta
import datetime


os.environ['OPENAI_API_KEY'] = os.getenv('OPENAI_API_KEY')

class ChatPDF:
    def __init__(self, json_path='memory.json', persist_directory="./chroma_db"):
        self.model = ChatOllama(model="llama3")
        self.text_splitter = RecursiveCharacterTextSplitter(chunk_size=1024, chunk_overlap=100)
        self.prompt = PromptTemplate.from_template(
            """
            <s> [INST] You are an assistant for question-answering tasks. Use the following pieces of retrieved context 
            to answer the question. If you don't know the answer, just say that you don't know. Use three sentences
             maximum and keep the answer concise. [/INST] </s> 
            [INST] Question: {question} 
            Context: {context} 
            Answer: [/INST]
            """
        )
        self.embedding = FastEmbedEmbeddings()
        self.json_path = json_path
        self.persist_directory = persist_directory
        self.memory_data = self._load_memory()
        
        # Configure OpenAI client
        self.openai_client = OpenAI()
        
        # Initialize or load the vector store
        self._initialize_vector_store()

    def _load_memory(self):
        if os.path.exists(self.json_path):
            with open(self.json_path, 'r') as f:
                try:
                    return json.load(f)
                except json.JSONDecodeError:
                    return []
        return []

    def _save_memory(self):
        with open(self.json_path, 'w') as f:
            json.dump(self.memory_data, f)

    def _initialize_vector_store(self):
        if os.path.exists(self.persist_directory):
            self.vector_store = Chroma(persist_directory=self.persist_directory, embedding_function=self.embedding)
        else:
            self.vector_store = Chroma(persist_directory=self.persist_directory, embedding_function=self.embedding)
            self.vector_store.persist()
        
        self.retriever = self.vector_store.as_retriever(
            search_type="similarity_score_threshold",
            search_kwargs={
                "k": 3,
                "score_threshold": 0.5,
            },
        )
        
        self.chain = ({"context": self.retriever, "question": RunnablePassthrough()}
                      | self.prompt
                      | self.model
                      | StrOutputParser())

    def ingest(self, pdf_file_path: str):
        docs = PyPDFLoader(file_path=pdf_file_path).load()
        self._process_documents(docs, source=pdf_file_path)

    def ingest_image(self, image: Image, image_name: str):
        # Convert image to base64
        buffered = io.BytesIO()
        image.save(buffered, format="JPEG")
        encoded_image = base64.b64encode(buffered.getvalue()).decode('utf-8')

        # Extract text using OpenAI
        response = self.openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Extract only text from this image"},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{encoded_image}"
                            },
                        },
                    ],
                }
            ],
            max_tokens=300,
        )

        extracted_text = response.choices[0].message.content

        if not extracted_text.strip():
            raise ValueError(f"No text found in the image {image_name}")

        doc = Document(page_content=extracted_text, metadata={"source": image_name})
        self._process_documents([doc], source=image_name)

        # Analyze the extracted text for actions
        self.analyze_text_for_actions(extracted_text)

    def _process_documents(self, docs, source):
        chunks = self.text_splitter.split_documents(docs)
        chunks = filter_complex_metadata(chunks)

        for chunk in chunks:
            self.memory_data.append({"source": source, "content": chunk.page_content})
            # Analyze each chunk for actions
            self.analyze_text_for_actions(chunk.page_content)

        self._save_memory()

        self.vector_store.add_documents(chunks)
        self.vector_store.persist()
    def check_vector_store_contents(self):
        all_docs = self.vector_store.get()
        print("Vector store contents:", all_docs)
    def ask(self, query: str):
        context = self.retriever.get_relevant_documents(query)
        print("Retrieved context:", context)
        self.check_vector_store_contents
        return self.chain.invoke(query)

    def clear(self):
        self.vector_store = None
        self.retriever = None
        self.chain = None
        self._initialize_vector_store()

    def analyze_text_for_actions(self, text):
        current_date = datetime.datetime.now()
    
        response = self.openai_client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "Analyze the following text and identify if any actions need to be taken. "
                                          "Specifically, look for: "
                                          "1. Dates of events or appointments "
                                          "2. Tasks or to-do items "
                                          "3. Reminders "
                                          f"Today's date is {current_date.strftime('%Y-%m-%d')}. "
                                          "For any dates mentioned in relative terms (e.g., 'this Friday,' 'next Monday'), "
                                          "calculate and provide the actual date. "
                                          "If an action is found, provide it in the following format: "
                                          "ACTION: [Type of action (e.g., SET_ALARM, ADD_TODO, SET_REMINDER)] "
                                          "DETAILS: [Relevant details including specific date (YYYY-MM-DD), time, and description] "
                                          "If no action is needed, respond with 'No action required.'"},
                {"role": "user", "content": text}
            ],
            max_tokens=150
        )

        analysis = response.choices[0].message.content

        if "ACTION:" in analysis:
            print("Action identified:")
            print(analysis)
            self.perform_action(analysis)
        else:
            print("No action required.")

    def perform_action(self, action_text):
        action_type = action_text.split("ACTION:")[1].split("\n")[0].strip()
        details = action_text.split("DETAILS:")[1].strip()

        if action_type == "SET_ALARM":
            self.set_alarm(details)
        elif action_type == "ADD_TODO":
            self.add_todo(details)
        elif action_type == "SET_REMINDER":
            self.set_reminder(details)

    def set_alarm(self, details):
        # This is a placeholder function. In a real application, you would integrate
        # with a calendar or alarm system.
        print(f"Alarm set: {details}")

    def add_todo(self, details):
        # This is a placeholder function. In a real application, you would integrate
        # with a to-do list or task management system.
        print(f"Todo added: {details}")

    def set_reminder(self, details):
        # This is a placeholder function. In a real application, you would integrate
        # with a reminder or notification system.
        print(f"Reminder set: {details}")