import tkinter as tk
import ttkbootstrap as ttk
import threading
import requests
import re
import json
import sseclient
import time
import os
from random import randint
from requests.exceptions import HTTPError,MissingSchema,InvalidSchema,InvalidURL,Timeout
from tkinter import filedialog
from ttkbootstrap.dialogs import Messagebox, Querybox
from ttkbootstrap.toast import ToastNotification
from ttkbootstrap.scrolled import ScrolledFrame, ScrolledText
from ttkbootstrap.tooltip import ToolTip
from ctypes import windll, byref, sizeof, c_int
from tkfontawesome import icon_to_image
from pathlib import Path
from collections import OrderedDict
from uuid import uuid4
from enum import StrEnum
from functools import partial
from dotenv import load_dotenv

#https://ttkbootstrap.readthedocs.io/en/latest/
#https://github.com/jshipley/TkFontAwesome/tree/main

#TODO: investigate bug regarding reasoning transcripts & colors.

load_dotenv(os.path.dirname(__file__)+"/.env")

VERSION = "V2"

BASE = os.getenv("API_BASE","https://openrouter.ai/api/v1/chat/completions")
KEY = os.getenv("API_KEY","")

MODEL = "anthropic/claude-sonnet-4.6"
EXTRA = {"reasoning":{"enabled":True}}

CTX = re.compile(r"⦓\d{4}⦔\s+(\w+): ([^⦓]*)",re.DOTALL)

FEATURES = ["temperature","frequency_penalty","presence_penalty","model","max_tokens"]

#Taken from blender colors, really should move this somewhere else at some point.
class Colors(StrEnum):
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

def darkmode(window):
    HWND = windll.user32.GetParent(window.winfo_id())
    DWMWA_USE_IMMERSIVE_DARK_MODE = 20
    windll.dwmapi.DwmSetWindowAttribute(HWND, DWMWA_USE_IMMERSIVE_DARK_MODE, byref(c_int(True)), sizeof(c_int))
    windll.shcore.SetProcessDpiAwareness(1)

def colormode(window,color):
    #Color is expected in the form of a "#rrggbb" string. Convert it to 0xbbggrr int here.
    color = int("0x00"+color[5:7]+color[3:5]+color[1:3],16)
    HWND = windll.user32.GetParent(window.winfo_id())
    DWMWA_CAPTION_COLOR = 35
    windll.dwmapi.DwmSetWindowAttribute(HWND, DWMWA_CAPTION_COLOR, byref(c_int(color)), sizeof(c_int))
    windll.shcore.SetProcessDpiAwareness(1)

class PlaintextViewer(ttk.Frame):
    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        self.textbox = tk.Text(self, borderwidth=0, wrap="word", font=("MS Gothic" if self.master.international_mode else "Consolas","10"))
        #self.textbox = tk.Text(self, borderwidth=0, wrap="word", font=('MS Gothic', 10))
        self.textbox.pack(side="left", fill="both", expand=True)
        self.scrollbar = ttk.Scrollbar(self, command=self.textbox.yview, bootstyle="round")
        self.scrollbar.pack(side="right", fill="y")
        self.textbox.configure(yscrollcommand=self.scrollbar.set)

    def tag_config(self, name, **kwargs):
        self.textbox.tag_config(name,**kwargs)
        self.textbox.tag_raise("sel")

    def render(self, entry, header="", tag=""):
        if header:
            self.textbox.insert("end", header.strip()+" ", ("b", tag))
        if entry:
            self.textbox.insert("end", entry, (tag,))
        self.textbox.see("end")

    def context(self):
        return [{"role":msg[0].lower(),"content":msg[1].strip()} for msg in re.findall(CTX,self.textbox.get("1.0","end").strip())]

    def clear(self):
        self.remove("1.0", "end")

    def remove(self, from_, to):
        self.textbox.delete(from_, to)

class Popout(ttk.Toplevel):
    def __init__(self,master,title,**kwargs):
        super().__init__(master,**kwargs)
        self.master = master
        self.colors = self.style.colors
        self.title(title)
        self.wm_iconbitmap(os.path.dirname(__file__)+"/icon.ico")
        colormode(self,self.colors.get("bg"))

class SPWindow(Popout):
    def __init__(self,master,**kwargs):
        kwargs['size'] = (700,650)
        kwargs['topmost'] = True
        kwargs['toolwindow'] = True
        super().__init__(master,"System Prompt",**kwargs)
        self.protocol("WM_DELETE_WINDOW", self.close)

        self.columnconfigure(0,weight=1)
        # self.rowconfigure(0,weight=1)
        # self.rowconfigure(1,weight=1)
        self.rowconfigure(2,weight=10)

        self.checks_frame = ttk.Frame(self)
        self.checks_frame.grid(column=0,row=0,sticky="news")

        self.check_sp = tk.BooleanVar(self,self.master.simple_sp)
        ttk.Checkbutton(self.checks_frame,bootstyle="round-toggle",variable=self.check_sp,command=self.check_switch,text="Simple System Prompt Mode").grid(column=0,row=0,padx=5,sticky="news")
        self.sp_cache = tk.BooleanVar(self,self.master.sp_cache)
        self.cache_checkbutton = ttk.Checkbutton(self.checks_frame,bootstyle="warning round-toggle",variable=self.sp_cache,command=self.cache_switch,
                        text="Cache System Prompt?",state="normal" if not self.check_sp.get() else "disabled")
        self.cache_checkbutton.grid(column=1,row=0,padx=5,sticky="news")
        
        self.add_img = icon_to_image("plus", fill=self.colors.get_foreground("default"), scale_to_height=16)
        self.import_img = icon_to_image("file-import", fill=self.colors.get_foreground("default"), scale_to_height=16)
        self.append_img = icon_to_image("file-plus", fill=self.colors.get_foreground("default"), scale_to_height=16)
        self.export_img = icon_to_image("file-export", fill=self.colors.get_foreground("default"), scale_to_height=16)
        self.buttons_frame = ttk.Frame(self)
        self.buttons_frame.grid(column=0,row=1,sticky="news")
        self.add_button = ttk.Button(self.buttons_frame,text="Add Part",image=self.add_img,compound="left",command=self.add_prompt_part,
                                     state="normal" if not self.check_sp.get() else "disabled")
        self.add_button.pack(side="left",expand=True,fill="x")
        self.import_button = ttk.Button(self.buttons_frame,text="Import Prompt",image=self.import_img,compound="left",command=self.import_prompt,
                                        state="normal" if not self.check_sp.get() else "disabled")
        self.import_button.pack(side="left",expand=True,fill="x")
        self.append_button = ttk.Button(self.buttons_frame,text="Append Prompt",image=self.append_img,compound="left",command=partial(self.import_prompt,overwrite=False),
                                        state="normal" if not self.check_sp.get() else "disabled")
        self.append_button.pack(side="left",expand=True,fill="x")
        self.export_button = ttk.Button(self.buttons_frame,text="Export Prompt",image=self.export_img,compound="left",command=self.export_prompt,
                                        state="normal" if not (self.check_sp.get() or len(self.master.system_prompt) == 0) else "disabled")
        self.export_button.pack(side="left",expand=True,fill="x")

        self.complex_sp_frame = ScrolledFrame(self,autohide=True)
        self.complex_sp_frame.grid(column=0,row=2,sticky="news")

        self.sp_parts = OrderedDict()
        if not self.master.simple_sp:
            for part in self.master.system_prompt:
                self.add_prompt_part(part['content'])

    def add_prompt_part(self,content=""):
        prompt_id = uuid4()
        prompt_frame = ttk.Frame(self.complex_sp_frame)
        prompt_frame.rowconfigure(0,weight=1)
        prompt_frame.columnconfigure(1,weight=10)
        prompt_text = ScrolledText(prompt_frame, wrap="word", borderwidth=0, height=5, autohide=True)
        prompt_text.insert("end", content)
        prompt_text.grid(row=0,column=1,sticky="news")
        prompt_text.button = ttk.Button(prompt_frame,image=self.master.root.delete_image,bootstyle="danger",command=lambda: self.remove_prompt_part(prompt_id))
        prompt_text.button.grid(row=0,column=0,sticky="news")
        prompt_frame.pack(side="top",expand=True,fill="both",padx=(0,15))
        self.sp_parts[prompt_id] = prompt_text
        if len(self.sp_parts) == 1:
            self.export_button.config(state="normal")

    def recalc_shrink(self):
        tmp = tk.Frame(self.complex_sp_frame, width=1, height=1, borderwidth=0, highlightthickness=0)
        tmp.pack()
        self.complex_sp_frame.update_idletasks()
        tmp.destroy()

    def remove_prompt_part(self,id):
        self.sp_parts.pop(id).master.destroy()
        self.recalc_shrink()
        if len(self.sp_parts) == 0:
            self.export_button.config(state="disabled")

    def import_prompt(self,overwrite=True):
        filename = filedialog.askopenfilename(filetypes=[("Prompts JSON","*.json")],defaultextension="*.json")
        if filename:
            with open(filename, encoding="utf-8") as file:
                import_sp = json.load(file)
            if import_sp.get("prompts",{}) == {}:
                ToastNotification("Error","Couldn't import prompts. Do they exist?",
                    duration="3000",alert=True,bootstyle="danger",
                    icon="\ue783",iconfont=("Segoe Fluent Icons","16")).show_toast()
                return
            if overwrite:
                if self.sp_parts: #Run only in the case that there are entries already in the list.
                    for part_id in list(self.sp_parts.keys()):
                        self.sp_parts.pop(part_id).master.destroy()
                    self.recalc_shrink()
                if import_sp.get("assistant_prefill","") != "":
                    self.master.prefill_input.insert("end", import_sp.get("assistant_prefill"))
                    print(self.master.prefill)
            for part in import_sp["prompts"]:
                self.add_prompt_part(part.get("content",""))
                
    def export_prompt(self):
        filename = filedialog.asksaveasfilename(filetypes=[("Prompts JSON","*.json")],defaultextension="*.json")
        if filename:
            with open(filename, mode="w", encoding="utf-8") as file:
                prompt_data = {"header":f"prompt import - v{VERSION}","prompts":[{"role":"system","content":part.get("1.0", "end-1c").rstrip()} for part in self.sp_parts.values()]}
                if self.master.prefill:
                    prompt_data["assistant_prefill"] = self.master.prefill
                json.dump(prompt_data,file,indent=2,ensure_ascii=False)

    def check_switch(self):
        if self.check_sp.get():
            self.master.system_prompt_input.configure(state="normal",fg=self.colors.get("inputfg"),bg=self.colors.get("inputbg"))
            self.add_button.configure(state="disabled")
            self.import_button.configure(state="disabled")
            self.append_button.configure(state="disabled")
            self.export_button.configure(state="disabled")
            self.cache_checkbutton.configure(state="disabled")
            self.sp_cache.set(False)
            self.master.sp_cache = False
            for part in self.sp_parts.values():
                part.text.configure(state="disabled",fg=self.colors.get("darkerinputfg"),bg=self.colors.get("darkerinputbg"))
                part.button.configure(state="disabled")
            self.master.simple_sp = True
        else:
            self.master.system_prompt_input.configure(state="disabled",fg=self.colors.get("darkerinputfg"),bg=self.colors.get("darkerinputbg"))
            self.add_button.configure(state="normal")
            self.import_button.configure(state="normal")
            self.append_button.configure(state="normal")
            if len(self.sp_parts) > 0:
                self.export_button.configure(state="normal")
            self.cache_checkbutton.configure(state="normal")
            for part in self.sp_parts.values():
                part.text.configure(state="normal",fg=self.colors.get("inputfg"),bg=self.colors.get("inputbg"))
                part.button.configure(state="normal")
            self.master.simple_sp = False

    def cache_switch(self):
        self.master.sp_cache = self.sp_cache.get()

    def close(self):
        if self.check_sp.get():
            self.master.system_prompt = self.master.system_prompt_input.get("1.0", "end-1c").rstrip()
        else:
            self.master.system_prompt = [{"role":"system","content":part.get("1.0", "end-1c").rstrip()} for part in self.sp_parts.values()]
        print(json.dumps(self.master.system_prompt,indent=4))
        print("Current SP Cache Mode: ",self.master.sp_cache)
        self.destroy()

class SettingsWindow(Popout):
    def __init__(self,master,**kwargs):
        #kwargs['size'] = (700,650)
        kwargs['topmost'] = True
        kwargs['toolwindow'] = True
        super().__init__(master,"Settings",**kwargs)
        self.master = master
        self.protocol("WM_DELETE_WINDOW", self.close)

        self.temperature = self.gen_slider_bars("Temperature",self.master.chat_properties["temperature"],bounds=(0,2),increment=0.05,
                                                tooltip="What sampling temperature to use, between 0 and 2. Higher values like 0.8 will make the output more random, while lower values like 0.2 will make it more focused and deterministic.")
        self.frequency_penalty = self.gen_slider_bars("Frequency Penalty",self.master.chat_properties["frequency_penalty"],bounds=(-2,2),increment=0.1,
                                                      tooltip="Positive values penalize new tokens based on their existing frequency in the text so far, decreasing the model's likelihood to repeat the same line verbatim.")
        self.presence_penalty = self.gen_slider_bars("Presence Penalty",self.master.chat_properties["presence_penalty"],bounds=(-2,2),increment=0.1,
                                                     tooltip="Positive values penalize new tokens based on whether they appear in the text so far, increasing the model's likelihood to talk about new topics.")
        
        model_frame = ttk.LabelFrame(self,text="Model Name")
        model_frame.pack(side="top",expand=True,fill="both")
        self.model = tk.StringVar(self,value=self.master.chat_properties["model"])
        ttk.Entry(model_frame,exportselection=False,textvariable=self.model).pack(side="left",expand=True,fill="both")
        ToolTip(model_frame,topmost=True,text="ID of the model to use.")

        max_tokens_frame = ttk.LabelFrame(self,text="Maximum Tokens")
        max_tokens_frame.pack(side="top",expand=True,fill="both")
        self.max_tokens = tk.IntVar(self,value=self.master.chat_properties["max_tokens"])
        ttk.Spinbox(max_tokens_frame,from_=0,to=4096,increment=1,textvariable=self.max_tokens).pack(side="left",expand=True,fill="both")
        ToolTip(max_tokens_frame,topmost=True,text="The maximum number of tokens that can be generated in the chat completion.")

        extra_frame = ttk.LabelFrame(self,text="Extra Options (JSON FORMAT ONLY)")
        extra_frame.pack(side="top",expand=True,fill="both")
        extra = json.dumps({k:v for k,v in self.master.chat_properties.items() if k not in FEATURES})
        self.extra_options = tk.StringVar(self,value=extra)
        ttk.Entry(extra_frame,exportselection=False,textvariable=self.extra_options).pack(side="left",expand=True,fill="both")
        ToolTip(extra_frame,topmost=True,text="Extra request body parameters. Must be JSON Format only. Mostly used for Gemini.")

        misc_frame = ttk.LabelFrame(self,text="Misc. Options")
        misc_frame.pack(side="top",expand=True,fill="both")
        self.international_mode = tk.BooleanVar(self,self.master.international_mode)
        ttk.Checkbutton(misc_frame,bootstyle="round-toggle",variable=self.international_mode,command=self.international_switch,text="International Mode").pack(side="left",expand=True,fill="both")
        # self.extra_options = tk.StringVar(self,value=extra)
        # ttk.Entry(extra_frame,exportselection=False,textvariable=self.extra_options).pack(side="left",expand=True,fill="both")
        # ToolTip(extra_frame,topmost=True,text="Extra request body parameters. Must be JSON Format only. Mostly used for Gemini.")

        debug_frame = ttk.LabelFrame(self,text="DEBUG BUTTONS")
        debug_frame.pack(side="top",expand=True,fill="both")
        ttk.Button(debug_frame, image=self.master.root.context_image, text="CTX", compound="left", width=4, command=lambda: print(json.dumps(self.master.context,indent=4,ensure_ascii=False))).grid(row=0, column=0, sticky="news")
        ttk.Button(debug_frame, image=self.master.root.context_image, text="SYS", compound="left", width=4, command=lambda: print(json.dumps(self.master.system_prompt,indent=4,ensure_ascii=False))).grid(row=0, column=1, sticky="news")
        ttk.Button(debug_frame, image=self.master.root.context_image, text="PFL", compound="left", width=4, command=lambda: print(json.dumps(self.master.prefill,indent=4,ensure_ascii=False))).grid(row=0, column=2, sticky="news")
        ttk.Button(debug_frame, image=self.master.root.context_image, text="USE", compound="left", width=4, command=lambda: self.master.uus_thread_handler()).grid(row=0, column=3, sticky="news")


    def gen_slider_bars(self,name,variable,bounds,increment,tooltip):
        frame = ttk.LabelFrame(self,text=name)
        frame.pack(side="top",expand=True,fill="both")
        var = tk.DoubleVar(self,value=variable)
        ttk.Scale(frame,from_=bounds[0],to=bounds[1],length=500,variable=var).pack(side="left",expand=True,fill="both")
        ttk.Spinbox(frame,from_=bounds[0],to=bounds[1],increment=increment,textvariable=var).pack(side="left",expand=True,fill="both")
        ToolTip(frame,text=tooltip,topmost=True)
        return var
    
    def international_switch(self):
        if self.international_mode.get():
            self.master.international_mode = True
            #There is probably a better way to do this, and if there's not, there should be.
            #However, I do not know what that is.
            self.master.viewer.textbox.configure(font=('MS Gothic', 10))
            self.master.editor.configure(font=('MS Gothic', 10))
        else:
            self.master.international_mode = False
            self.master.viewer.textbox.configure(font=("Consolas","10"))
            self.master.editor.configure(font=('Cascadia Mono', 10))

    def close(self):
        chat_properties_temp = {"temperature":self.temperature.get(),
         "frequency_penalty":self.frequency_penalty.get(),
         "presence_penalty":self.presence_penalty.get(),
         "model":self.model.get(),
         "max_tokens":self.max_tokens.get()}
        try:
            chat_properties_temp.update(json.loads(self.extra_options.get()))
        except json.JSONDecodeError as e:
            print(e)
            ToastNotification("Error","Make sure Extra Options are properly formatted JSON.",
                                  duration="3000",alert=True,bootstyle="danger",
                                  icon="\ue783",iconfont=("Segoe Fluent Icons","16")).show_toast()
            return
        self.master.chat_properties = chat_properties_temp
        print(json.dumps(self.master.chat_properties,indent=4))
        self.destroy()

class ChatInstance(ttk.Frame):
    def __init__(self,master,id,**kwargs):

        self.context = kwargs.pop("context",[])
        self.prefill = kwargs.pop("prefill","")
        
        if system_prompt := kwargs.pop("system_prompt",None):
            self.system_prompt = system_prompt
            self.simple_sp = (type(self.system_prompt) == str)
        else:
            self.system_prompt = "Help the user with whatever they ask."
            self.simple_sp = True
        self.sp_cache = kwargs.pop("sp_cache",False)
        print("Current SP Cache Mode:", self.sp_cache)

        temp_user_cache_active = kwargs.pop("user_cache_active",False) #Can't use tkinter constructs before initialization.
        self.user_cache_location = kwargs.pop("user_cache_location",None)
        self.user_cache_timeout = kwargs.pop("user_cache_timeout",None)
        print("Current User Cache Mode:", temp_user_cache_active)

        self.chat_properties = kwargs.pop("chat_properties",{"temperature":0.88,"frequency_penalty":0.03,"presence_penalty":0.05,"model":MODEL,"max_tokens":2048})
        extra = kwargs.pop("extra_properties",None)
        if type(extra) == dict:
            self.chat_properties.update(extra)
        else:
            self.chat_properties.update(EXTRA)

        self.id = id
        self.root = kwargs.pop("root",master)
        self.international_mode = kwargs.pop("international",False)
        super().__init__(master, **kwargs)
        self.user_cache_active = tk.BooleanVar(self,temp_user_cache_active)

        self.rowconfigure(0, weight=3)
        self.rowconfigure(1, weight=30)
        self.rowconfigure(2, weight=5)
        self.columnconfigure(0, weight=1)

        self.prompt_frame = self.create_prompt_frame()
        self.prompt_frame.grid(row=0, column=0, sticky="news")
        
        self.viewer = PlaintextViewer(self)
        self.viewer.grid(row=1, column=0, sticky="news")
        self.viewer.tag_config("system", background=self.root.colors.get('darkerinputbg'), foreground=self.root.colors.get('darkerinputfg'))

        self.parse_context()
        self._ctx_update_after_id = None
        self._ctx_freeze = False
        self.viewer.textbox.bind("<<Modified>>",lambda event: self.context_wait_handler())

        #Potential problem: if two or more threads are generating at the same time, stopping one might stop both.
        #Not sute how to fix.
        self.event_parse_stop = threading.Event()

        self.edit_frame = self.create_edit_frame()
        self.bind("<<update_usage>>",self.uus_gui_handler)
        self.update_usage_statstics()
        self.key_unset = False
    
    def uus_thread_handler(self):
        threading.Thread(target=self.update_usage_statstics,daemon=True).start()

    def uus_gui_handler(self,event):
        new_credit_amount = event.x #This is stupid and the only way that this seems to work.
        if new_credit_amount == -5000:
            new_credit_amount = "Credits: ERR"
        else:
            new_credit_amount = f"Credits: {new_credit_amount/1000:.3f}"
        self.remaining_usage.set(new_credit_amount)
    
    def update_usage_statstics(self):
            resp = requests.get("https://openrouter.ai/api/v1/credits",headers={"authorization": f"Bearer {KEY}"})
            if resp.status_code == 200:
                #newcred = f"Credits: {resp.json()["data"]["limit_remaining"]:.3f}"
                data = resp.json()["data"]
                self.event_generate("<<update_usage>>",x=(data["total_credits"]-data["total_usage"])*1000)
                #self.remaining_usage.set(newcred)
            else:
                print(Colors.FAIL+Colors.UNDERLINE+"Key Error"+Colors.ENDC+": "+Colors.FAIL+resp.json()["error"]["message"]+Colors.ENDC)
                self.event_generate("<<update_usage>>",x=-5000)
                #self.remaining_usage.set("Credits: ERR")

    def context_wait_handler(self):
        if self._ctx_freeze:
            print(Colors.WARNING+"Context frozen - no modification occuring."+Colors.ENDC)
            return
        print("passing into wait handler.")
        if self._ctx_update_after_id is not None:
            print("killing previous update")
            self.after_cancel(self._ctx_update_after_id)
        if not self.viewer.textbox.edit_modified():
            print("No need to modify context - already occured.")
            return
        self._ctx_update_after_id = self.after(1000, lambda: self.update_context())
    
    def update_context(self, force=False):
        print("Passing into context updater.")
        if self.viewer.textbox.edit_modified() or force:
            print("Updating context.") #DEBUG
            self.context = self.viewer.context()
            self.viewer.textbox.edit_modified(False)
        else:
            print("No modification actually occured to context - do nothing.")

    def create_prompt_frame(self):
        frame = ttk.Frame(self)
        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(1, weight=1)

        ttk.Button(frame, text="System Prompt",command=self.open_sp_window).grid(row=0, column=0, sticky="ew")
        ttk.Label(frame, text="Assistant Prefill",anchor="center").grid(row=0, column=1, sticky="ew")

        self._update_after_id = None

        self.system_prompt_input = ttk.Text(frame, wrap="word", height=2, borderwidth=0)
        self.system_prompt_input.grid(row=1, column=0, sticky="news")
        if self.simple_sp:
            self.system_prompt_input.insert("end", self.system_prompt)
        else:
            self.system_prompt_input.configure(state="disabled",fg=self.root.colors.get("darkerinputfg"),bg=self.root.colors.get("darkerinputbg"))
        self.system_prompt_input.bind("<<Modified>>",lambda event: self.prompt_field_wait_handler("system_prompt"))

        self.prefill_input = tk.Text(frame, wrap="word", height=2, borderwidth=0)
        self.prefill_input.grid(row=1, column=1, sticky="news")
        self.prefill_input.insert("end", self.prefill)
        self.prefill_input.bind("<<Modified>>",lambda event: self.prompt_field_wait_handler("prefill"))

        return frame
    
    def open_sp_window(self):
        SPWindow(self)
    
    def prompt_field_wait_handler(self,prompt_field):
        if self._update_after_id is not None:
            self.after_cancel(self._update_after_id)
        if not getattr(self,prompt_field+"_input").edit_modified():
            print("No need to modify - already occured.")
            return
        self._update_after_id = self.after(1000, lambda: self.update_prompt_field(prompt_field))
    
    def update_prompt_field(self,prompt_field):
        if (prompt_field != "system_prompt") and (prompt_field != "prefill"):
            raise ValueError(f"Incorrect value passed to prompt_field argument: {prompt_field}. Only 'system_prompt' and 'prefill' accepted.")
        print(f"setting: {prompt_field}")
        prompt_field_input = getattr(self,prompt_field+"_input")
        if not prompt_field_input.edit_modified():
            print("No modification actually occured - do nothing.")
            return
        setattr(self, prompt_field, prompt_field_input.get("1.0", "end-1c").rstrip())
        prompt_field_input.edit_modified(False)
        print(f"Field is now: {getattr(self,prompt_field)}")

    def create_edit_frame(self):
        frame = ttk.Frame(self, borderwidth=1, relief="solid")
        frame.grid(row=2, column=0, sticky="news")
        frame.rowconfigure(2, weight=1)
        frame.columnconfigure(0, weight=1)

        self.editor = tk.Text(frame, wrap="word", height=5, borderwidth=0, undo=True, font=("MS Gothic" if self.international_mode else "Cascadia Mono","10"))
        #self.editor = tk.Text(frame, wrap="word", height=5, borderwidth=0, font=('MS Gothic', 10))
        self.editor.grid(row=2, column=0, sticky="news")
        self.editor.bind("<Shift-Return>", self.parse_user_input)

        self.progress_bar = ttk.Progressbar(frame, mode="indeterminate")
        frame.rowconfigure(1, minsize=self.progress_bar.winfo_reqheight())

        self.create_lower_menu(frame)

        return frame

    def create_lower_menu(self, frame):
        menu_frame = ttk.Frame(frame)
        menu_frame.grid(row=0, column=0, sticky="news")
        for i in range(12):
            menu_frame.columnconfigure(i, weight=2)
        menu_frame.columnconfigure(20, weight=1)
        menu_frame.columnconfigure(21, weight=2)
        menu_frame.columnconfigure(30, weight=20)
        menu_frame.columnconfigure(31, weight=2)

        ttk.Button(menu_frame, image=self.root.settings_image, width=1, command=self.change_instance_settings).grid(row=0, column=0, sticky="news")
        ttk.Button(menu_frame, text="NEW", image=self.root.new_image, compound="left", width=3, command=self.new_session).grid(row=0, column=1, sticky="news")
        ttk.Button(menu_frame, text="SAVE", image=self.root.save_image, compound="left", width=4, command=self.save_context).grid(row=0, column=2, sticky="news")
        ttk.Button(menu_frame, text="LOAD", image=self.root.load_image, compound="left", width=4, command=self.load_context).grid(row=0, column=3, sticky="news")
        ttk.Button(menu_frame, text="RENAME", image=self.root.rename_image, compound="left", width=6, command=self.rename_session).grid(row=0, column=4, sticky="news")
        ttk.Button(menu_frame, text="BRANCH", image=self.root.branch_image, compound="left", width=6, command=self.create_branch).grid(row=0, column=5, sticky="news")
        ttk.Button(menu_frame, text="BACK", width=4, image=self.root.back_image, compound="left", command=self.one_message_back, bootstyle="warning").grid(row=0, column=6, sticky="news")
        ttk.Button(menu_frame, text="REROLL", image=self.root.reroll_image, compound="left", width=5, command=self.reroll_last_message, bootstyle="warning").grid(row=0, column=7, sticky="news")
        self.user_cache_button = ttk.Checkbutton(menu_frame, text="CACHE", image=self.root.cache_image_unset, compound="left", width=5, variable=self.user_cache_active, command=self.user_cache_toggle, bootstyle="warning outline toolbutton")
        self.user_cache_button.grid(row=0, column=8, sticky="news")
        self.user_cache_button.bind("<Enter>",lambda event: self.user_cache_button.config(image=self.root.cache_image_set))
        self.user_cache_button.bind("<Leave>",lambda event: self.user_cache_button.config(image=self.root.cache_image_unset))
        ttk.Button(menu_frame, text="CLEAR", width=4, image=self.root.clear_image, compound="left", command=self.restart_session, bootstyle="danger").grid(row=0, column=9, sticky="news")
        ttk.Button(menu_frame, text="DELETE", width=5, image=self.root.delete_image, compound="left", command=self.delete_session, bootstyle="danger").grid(row=0, column=10, sticky="news")
    
        self.remaining_usage = tk.StringVar(self,value="Credits: ERR")
        ttk.Label(menu_frame,textvariable=self.remaining_usage,width=-14).grid(row=0, column=21, sticky="news")

        #Should be able to put the start and stop buttons in the same place at different times.
        self.button_parse_stop = ttk.Button(menu_frame, text="STOP", width=5, image=self.root.stop_image, compound="right", command=lambda: self.event_parse_stop.set(), bootstyle="warning")
        self.button_parse_send = ttk.Button(menu_frame, text="SEND", width=5, image=self.root.send_image, compound="right", command=self.parse_user_input)
        self.button_parse_send.grid(row=0, column=31, sticky="news")
        menu_frame.columnconfigure(31, minsize=min(self.button_parse_stop.winfo_reqwidth(),self.button_parse_send.winfo_reqwidth()))

    def parse_context(self):
        for i, entry in enumerate(self.context):
            self.viewer.render(entry['content'].strip()+"\n",f"⦓{i:0>4}⦔ {entry['role'].title():>9}: ",entry["role"])

    def parse_user_input(self, *args):
        if not self._ctx_freeze:
            user_message = self.editor.get("1.0", "end-1c").rstrip()
            self.editor.delete("1.0", "end")
            if user_message:
                self.viewer.render(user_message.strip()+"\n",f"⦓{len(self.context)//2:0>4}⦔ {'User':>9}: ","user")
                self.context.append({"role": "user", "content": user_message})
                self.viewer.textbox.edit_modified(False) #Tell the viewer that we've handled this one and it doesn't need to regen context.

            threading.Thread(target=self.parse_assistant_output,daemon=True).start()
        else:
            print(Colors.WARNING + "Context frozen - cannot pass new messages in." + Colors.ENDC)
        return "break"

    def parse_assistant_output(self):
        self.progress_bar.start()
        self.progress_bar.grid(row=1, column=0, sticky="news")
        self.button_parse_send.grid_forget()
        self.button_parse_stop.grid(row=0, column=31, sticky="news")
        self._ctx_freeze = True #Freeze context because we're adding a bunch of smaller messages, and will update it all at the end.
        self.chat_error = False

        self.viewer.render("",f"⦓{len(self.context)//2:0>4}⦔ {'Assistant':>9}: ","assistant") #Add header:

        for message in self.chat():
            self.viewer.render(message,"","assistant")
            if self.event_parse_stop.is_set():
                self.viewer.render("\n","","assistant")
                break
        
        self.event_parse_stop.clear()
        self.update_context() #update and unfreeze context with the new message.
        self._ctx_freeze = False

        self.progress_bar.stop()
        self.progress_bar.grid_forget()
        self.button_parse_stop.grid_forget()
        self.button_parse_send.grid(row=0, column=31, sticky="news")
        
        if not self.chat_error:
            self.uus_thread_handler() #Lets see if this works better.

    def chat(self,timeout=0):
        #generate payload.
        try:
            payload = {}
            payload.update(self.chat_properties)
            if not self.simple_sp:
                system_prompt = {"role":"system","content":[{"type":"text","text":part["content"]} for part in self.system_prompt]}
                if self.sp_cache:
                    system_prompt["content"].append({"type":"text","text":"<!--End of system prompt-->","cache_control":{"type":"ephemeral"}})
            else:
                system_prompt = {"role":"system","content": self.system_prompt}
            prefill = ([{"role": "assistant", "content": self.prefill}] if self.prefill else [])

            if self.user_cache_active.get():
                if (not self.user_cache_timeout) or (time.time() > self.user_cache_timeout): #This should update when pressing the button, or when the cache times out.
                    print(Colors.WARNING+"Updating User Cache Location."+Colors.ENDC)
                    print("User Cache Location:", len(self.context)-1)
                    self.user_cache_location = len(self.context)-1 #Put cache point at end of current context.
                self.user_cache_timeout = time.time() + 5 * 60 #five minute timeout (refreshes each time cached content is queried)
                self.context[self.user_cache_location]["content"] = [{"type":"text","text":self.context[self.user_cache_location]["content"]},{"type":"text","text":"<!--User Cache Point (ignore this)-->","cache_control":{"type":"ephemeral"}}]

            payload["messages"] = [system_prompt] + self.context + prefill
            payload["stream"] = True
            #print(json.dumps(payload,indent=4)) #DEBUG
            #print("User Cache Location:", len(self.context)-1)

            headers = {
                "x-title": "mpChat windows V-"+VERSION,
                "x-api-key": KEY, 
                "Authorization" : "Bearer " + KEY,
                "User-Agent": "Mozilla/1.0 (Win3.1)"
                }

            resp = requests.post(BASE,headers=headers,json=payload,stream=True, timeout=60)
            resp.raise_for_status()

            try:
                client = sseclient.SSEClient(resp)
                for event in client.events():
                    match event.data:
                        case '[DONE]':
                            print(Colors.ENDC)
                            yield "\n"
                        case _:
                            data = json.loads(event.data)
                            if e := data.get("error"):
                                print(Colors.FAIL+Colors.UNDERLINE+"Endpoint Error"+Colors.ENDC+": "+Colors.FAIL+e["message"]+Colors.ENDC)
                                print(e)
                                self.chat_error = True
                                yield "Endpoint error, check console.\n"
                            if u := data.get("usage"):
                                print("\n"+Colors.ENDC+Colors.UNDERLINE+"TOKEN USAGE"+Colors.ENDC)
                                # create = u.get("cache_creation_input_tokens",0)
                                # print((Colors.WARNING if create else "")+f"create: {create:,}")
                                # read = u.get("cache_read_input_tokens",0)
                                # print((Colors.OKGREEN if read else "")+f"read: {read:,}")
                                prompt = u["prompt_tokens"]
                                print((Colors.WARNING if prompt >= 20000 else "")+f"input: {prompt:,}"+Colors.ENDC)
                                completion = u["completion_tokens"]
                                print(f"completion: {completion:,}")

                            # #These two blocks might not exist when streaming a request. Commented out.
                            # #Kind of a shame - I would have liked to see the reasoning.
                            # elif data.get("refusal"):
                            #         print(Colors.FAIL+"Endpoint Refusal\n"+Colors.ENDC+json.dumps(data["refusal"],indent=4))
                            # if data.get("reasoning"):
                            #     print(json.dumps(data["reasoning"],indent=4))

                            delta = next(iter(data.get("choices",{})), {}).get("delta",{})

                            if delta.get("reasoning",""):
                                print(Colors.OKCYAN+delta['reasoning'],end="")
                                
                            yield delta.get("content","")
                            # #load data as json
                            # #then go into "choices" list from upper dictionary (garunteed to be available)
                            # #then try to get first entry of "choices" list (not garunteed), or {} if not available
                            # #then try to get "delta" dictionary from that, or {} if not available
                            # #then try to get "content" string from that, or "" if not available
                            # #strip any trailing whitespace
                            # #then yield
            except (Timeout, ConnectionError) as e:
                print(str(e))
                yield "Streaming Error. Please try again later.\n"

        except HTTPError:
            r = resp.json()
            if r.get("error"):
                print(Colors.FAIL+Colors.UNDERLINE+"Endpoint Error"+Colors.ENDC+": "+Colors.FAIL+r["error"]["message"]+Colors.ENDC)
                yield "Endpoint error, check console.\n"
            else:
                print(resp.json())
                yield "Problem with upstream generation function. Please try again later.\n"
        except (MissingSchema, InvalidSchema, InvalidURL):
            yield "Problem with URL. Please check.\n"
        except (Timeout, ConnectionError):
            # #I don't think this will actually work due to stream interuptions.
            # if timeout < 3:
            #     print(f"Retrying due to connection timeout. Attempt {timeout+1}")
            #     return self.chat(timeout=timeout+1)
            yield "Connection error. Please try again later.\n"

    def one_message_back(self):
        #print("This is probably not depricated, but it has issues.")
        if not self.context: #If there's nothing in the context, obviously there's no need to delete anything.
            return
        last_assistant_range = self.viewer.textbox.tag_prevrange("assistant","end")
        last_user_range = self.viewer.textbox.tag_prevrange("user","end")
        if last_assistant_range and last_user_range: #Make sure at least one assistant message and at least one user message was found.
            if self.viewer.textbox.compare(last_assistant_range[0],"==",last_user_range[1]): #Check if they're co-located, and if so, remove.
                self.viewer.remove(*last_assistant_range)
                self.viewer.remove(*last_user_range)
                self.context = self.context[:-2]
                self.viewer.textbox.edit_modified(False) #Tell the viewer that we've handled this one and it doesn't need to regen context.
            else:
                #TODO, maybe?
                pass #Gather more assistant messages up to the last user message. Remove all.

    def reroll_last_message(self):
        if not self._ctx_freeze:
            last_assistant_range = self.viewer.textbox.tag_prevrange("assistant","end")
            last_user_range = self.viewer.textbox.tag_prevrange("user","end")
            if last_assistant_range and last_user_range: #Make sure at least one assistant message and at least one user message was found.
                if self.viewer.textbox.compare(last_assistant_range[0],"==",last_user_range[1]): #If there's an assistant message already, remove it.
                    self.viewer.remove(*last_assistant_range)
                    self.context.pop()
                    self.viewer.textbox.edit_modified(False) #Tell the viewer that we've handled this one and it doesn't need to regen context.
                print(self.context)
                threading.Thread(target=self.parse_assistant_output,daemon=True).start()
        else:
            print(Colors.WARNING + "Can't reroll while context frozen." + Colors.ENDC)

    def user_cache_toggle(self):
        print("Cache Variable is now ", self.user_cache_active.get())
        if self.user_cache_active.get():
            print(Colors.WARNING+"User Cache now Active."+Colors.ENDC)
            self.user_cache_button.unbind("<Enter>")
            self.user_cache_button.unbind("<Leave>")
        else:
            print(Colors.WARNING+"User Cache now Inactive."+Colors.ENDC)
            self.user_cache_button.bind("<Enter>",lambda event: self.user_cache_button.config(image=self.root.cache_image_set))
            self.user_cache_button.bind("<Leave>",lambda event: self.user_cache_button.config(image=self.root.cache_image_unset))
            self.user_cache_location = None
            self.user_cache_timeout = None

    def change_instance_settings(self):
        SettingsWindow(self)

    def restart_session(self):
        answer = Messagebox.okcancel("Restart this session?","Confirm")
        if answer and (answer.lower() == "ok"):
            self.context.clear()
            self.viewer.clear()
            self.viewer.textbox.edit_modified(False) #Tell the viewer that we've handled this one.
            self.editor.delete("1.0", "end")
            self.editor.focus_set()

    def save_context(self):
            filetypes = [("JSON", "*.json"), ("Text", "*.txt")]
            filename = filedialog.asksaveasfilename(filetypes=filetypes, defaultextension=".json")
            if filename:
                self.update_context(force=True)
                if filename.lower().endswith(".json"):
                    with open(filename, "w", encoding="utf-8") as file:
                        chat_properties = self.chat_properties.copy()
                        session_data = {"system_prompt":self.system_prompt,"prefill":self.prefill,"chat_properties":chat_properties,"context":self.context}
                        json.dump(session_data, file, indent=2, ensure_ascii=False)
                else:
                    with open(filename, "w", encoding="utf-8") as file:
                        lines = [f"{message['role']}: {message['content']}" for message in self.context]
                        file.write("\n".join(lines))

    def new_session(self,name="New Session",**kwargs):
        new_instance = ChatInstance(master = self.master,
                        root = self.root,
                        id = len(self.root.chats),
                        **kwargs)
        self.master.add(new_instance,text=name)
        self.master.select(new_instance.id)
        self.root.chats.append(new_instance)

    def load_context(self):
        #threading.Thread(target=self.load_threaded,daemon=True).start()
        self.load_threaded() #Tkinter isn't thread-safe, so we'll figure out how to make the code thread-safe later.
    
    def load_threaded(self):
        self.progress_bar.start()
        self.progress_bar.grid(row=1, column=0, sticky="news")

        filetypes = [("JSON","*.json")]
        filename = filedialog.askopenfilename(filetypes=filetypes, defaultextension=".json")
        if filename:
            with open(filename, "r", encoding="utf-8") as file:
                session_data = json.load(file)
                if "base_properties" in session_data.keys():
                    session_data["chat_properties"] = session_data.pop("base_properties")
            ##Openrouter Override
            self.new_session(name = Path(filename).stem, **session_data)

        self.progress_bar.stop()
        self.progress_bar.grid_forget()

    def create_branch(self):
        #threading.Thread(target=self.branch_threaded,daemon=True).start()
        self.branch_threaded() #Tkinter isn't thread-safe, so we'll figure out how to make the code thread-safe later.

    def branch_threaded(self):
        self.progress_bar.start()
        self.progress_bar.grid(row=1, column=0, sticky="news")

        name = self.master.tab(self.id)['text']
        extra = {k:v for k,v in self.chat_properties.items() if k not in FEATURES}
        self.new_session(name = f"{name} - Branch", context = self.context[:], system_prompt = self.system_prompt, prefill = self.prefill,
                         chat_properties = self.chat_properties, extra_properties = extra, sp_cache = self.sp_cache, user_cache_active=self.user_cache_active.get(),
                         user_cache_location=self.user_cache_location, user_cache_timeout=self.user_cache_timeout, international = self.international_mode)
        self.progress_bar.stop()
        self.progress_bar.grid_forget()

    def delete_session(self):
        answer = Messagebox.okcancel("Delete this session?","Confirm")
        if answer and (answer.lower() == "ok"):
            if len(self.root.chats) == 1:
                ToastNotification("Error","Unable to delete all chat sessions.",
                                  duration="3000",alert=True,bootstyle="danger",
                                  icon="\ue783",iconfont=("Segoe Fluent Icons","16")).show_toast()
                return
            for chat in self.root.chats[self.id+1:]:
                if chat.id > self.id:
                    chat.id -= 1
            self.master.forget(self)
            self.root.chats.remove(self)
            del self

    def rename_session(self):
        print(self.id)
        name = self.master.tab(self.id)['text']
        print(name)
        new_name = Querybox().get_string("New chat name: ","Rename",name)
        self.master.tab(self.id,text=new_name)

class ChatApp(ttk.Window):
    def __init__(self):
        super().__init__(themename="superhero")
        self.style.load_user_themes(os.path.dirname(__file__)+"/theme.json")
        self.style.theme_use("vimix-jade")
        self.withdraw() #hide the window
        self.after(0,self.deiconify)
        self.title("Chat")
        self.wm_iconbitmap(os.path.dirname(__file__)+"/icon.ico")
        self.geometry("1250x750+200+125")

        self.colors = self.style.colors
        self.colors.set("darkerinputbg",self.colors.update_hsv(self.colors.inputbg, vd=-0.30))
        self.colors.set("darkerinputfg",self.colors.update_hsv(self.colors.inputfg, vd=-0.30))

        print(self.style.lookup('TButton','font'))
        self.style.configure('TButton', font=('Segoe UI Semibold',9))

        self.settings_image = icon_to_image("gear", fill=self.colors.get_foreground("default"), scale_to_width=16)
        self.context_image = icon_to_image("message-dots", fill=self.colors.get_foreground("default"), scale_to_width=16)
        self.new_image = icon_to_image("message-plus",fill=self.colors.get_foreground("default"), scale_to_width=16)
        self.save_image = icon_to_image("floppy-disk",fill=self.colors.get_foreground("default"), scale_to_width=16)
        self.load_image = icon_to_image("folder-open",fill=self.colors.get_foreground("default"), scale_to_width=16)
        self.rename_image = icon_to_image("pen",fill=self.colors.get_foreground("default"), scale_to_width=16)
        self.branch_image = icon_to_image("code-branch",fill=self.colors.get_foreground("default"), scale_to_width=16)
        self.back_image = icon_to_image("delete-left",fill=self.colors.get_foreground("warning"), scale_to_width=16)
        self.edit_image = icon_to_image("scissors",fill=self.colors.get_foreground("warning"), scale_to_width=16)
        self.reroll_image = icon_to_image("dice",fill=self.colors.get_foreground("warning"), scale_to_width=16)
        self.clear_image = icon_to_image("broom-wide",fill=self.colors.get_foreground("danger"), scale_to_width=16)
        self.delete_image = icon_to_image("trash",fill=self.colors.get_foreground("danger"), scale_to_width=16)
        self.send_image = icon_to_image("paper-plane-top",fill=self.colors.get_foreground("default"), scale_to_width=16)
        self.stop_image = icon_to_image("octagon-exclamation",fill=self.colors.get_foreground("default"), scale_to_width=16)
        self.cache_image_unset = icon_to_image("database",fill=self.colors.warning, scale_to_width=16)
        self.cache_image_set = icon_to_image("database",fill=self.colors.get_foreground("default"), scale_to_width=16)

        self.chat_tabs = ttk.Notebook(self)
        self.chat_tabs.pack(expand=True,fill="both")

        self.chats = []

        init_zero = ChatInstance(self.chat_tabs,0,root=self)
        self.chat_tabs.add(init_zero,text="Chat 0")
        self.chats.append(init_zero)
        
if __name__ == "__main__":
    #Setting taskbar icon
    #https://stackoverflow.com/q/1551605
    appid = f"authoritas.chatsys.subproduct.{VERSION}"
    windll.shell32.SetCurrentProcessExplicitAppUserModelID(appid)
    app = ChatApp()
    colormode(app,app.colors.get("bg"))
    #darkmode(app)
    app.mainloop()