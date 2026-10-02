# METIS

Program to catalogue and visualize a codebase, based on the files, the code, functions and how they connect to each other. Visualized in a GUI that allows for an easily digestible overview.

## Setup

- Code is based on python
- information about files, functions and connections will be stored in JSON files.
- Minimalist GUI
- Markdown converter -> Information that's retreived from JSON will be assembled in MARKDOWN for easy visualization.
- Utilizing GIT to maintain codebase and branches, use diff to show difference between branches, 

## Rough Pitch

I want a program that helps visualizing Codebases and how files and functions they connect to each other. Projects are filed not as one repo per project, so one project can contain multiple repositories and allows for a visualization of their interaction, if one depends on the other. 

The idea is a mix of scripts and AI (when the user has access to an AI API). Once a project has been established and all the repos are pulled, a script gets an overview of the code structure, like folder and file position via tree, filetypes and their programming language, as much information as is possible through a script to get. What functions are importet, which function / method points to which other file in the codebase or to something in another repo, etc. the initial overview is then stored in JSON, both to be used for the visualization as well as for the AI. 

Where AI comes into play is in interpreting the data. For that it might need certain skills that come with the program. What the AI is supposed to do is study the codebase, read the README and other docs (if documentation is provided), use the information from the initial "scan" to find everything it needs, and build a more information dense overview from that, also stored in JSON again.

So a workflow with this program should look like this: 

Open Program -> Add New Project -> paste 1 or more links to github repos OR paths to local folders -> if Github repo, pull repo and branches -> then click on "Local Scan" -> the script gets as much information as possible and builds the first layer of visualization: Folder structure, collapsible, i can see all the repos and files, click on them to open some basic information the initial scan collected -> that information overview is interpreted markdown files (easy to code for, looks neat) -> then click "AI Scan" -> AI (like Claude) uses its skills and the already collected information to build a deeper knowledge base, finds out what each file actually "does", what it points to, which function is connected to which file etc. -> Now clicking on a File shows things like "Migration script - Python" and underneath shows a concise explanation like "Script to migrate customer Data from legacy Database to new Cluster" followed by code snippets for functions and when they draw stuff from other files or send stuff to other files, a click on the function opens a new overview of everthing this connects to, and i can click on each item to see information of said file. Basically a compendium of the whole code structure that i can click through like a wiki. 

Also other functions that best utilize the Diff-Function and also allows for the AI to distinguish between branches, but also to explain changes in the codebase. 

The main purpose of the Application is to assist a novice programmer in understanding a complex codebase with visual aids that help understand how files are connected and where to look for when trying to apply changes. 




