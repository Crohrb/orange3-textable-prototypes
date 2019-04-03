"""
Class Childes
Copyright 2019 University of Lausanne
-----------------------------------------------------------------------------
This file is part of the Orange3-Textable-Prototypes package.

Orange3-Textable-Prototypes is free software: you can redistribute it
and/or modify it under the terms of the GNU General Public License as published
by the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

Orange3-Textable-Prototypes is distributed in the hope that it will be
useful, but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with Orange-Textable-Prototypes. If not, see
<http://www.gnu.org/licenses/>.
"""

__version__ = u"0.0.2"
__author__ = "Aris Xanthos"
__maintainer__ = "Aris Xanthos"
__email__ = "aris.xanthos@unil.ch"


import os
import io
import re
import pickle
import inspect
import zipfile
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup

from Orange.widgets import widget, gui, settings

from LTTL.Segmentation import Segmentation
from LTTL.Segment import Segment
from LTTL.Input import Input
import LTTL.Segmenter as Segmenter

from _textable.widgets.TextableUtils import (
    OWTextableBaseWidget, VersionedSettingsHandler, pluralize,
    InfoBox, SendButton, ProgressBar
)


class Childes(OWTextableBaseWidget):
    """Textable widget for importing data in XML format from the CHILDES
    database (https://childes.talkbank.org/data-xml/).
    """

    #----------------------------------------------------------------------
    # Widget's metadata...

    name = "CHILDES"
    description = "Import XML data from the CHILDES database"
    icon = "icons/CHILDES.svg"
    priority = 12

    #----------------------------------------------------------------------
    # Channel definitions (NB: no input in this case)...

    inputs = []
    outputs = [
        ("Files", Segmentation, widget.Default),
        ("Utterances", Segmentation),
        ("Words", Segmentation),
    ]

    #----------------------------------------------------------------------
    # Settings...

    settingsHandler = VersionedSettingsHandler(
        version=__version__.rsplit(".", 1)[0]
    )

    importedCorpora = settings.Setting(list())
    outputUtterances = settings.Setting(False)
    outputWords = settings.Setting(False)
    includePrefixes = settings.Setting(False)
    includePOSTag = settings.Setting(False)
    autoSend = settings.Setting(False)

    #----------------------------------------------------------------------
    # Other class variables...
    
    baseUrl = "https://childes.talkbank.org/data-xml/"
    cacheFilename = "cache_childes"
    cachedFoldername = "cached_childes_corpora"

    want_main_area = False

    def __init__(self):
        """Widget creator."""

        super().__init__()

        # Other (non-setting) attributes...
        self.fileSegmentation = None
        self.createdInputs = list()
        self.displayedFolderLabels = list()
        self.currentFolder = self.__class__.baseUrl
        self.database = None
        self.selectedInDisplayedFolder = list()
        self.selectedInSelection = list()
        self.selectionLabels = list()
        

        # Next two instructions are helpers from TextableUtils. Corresponding
        # interface elements are declared here and actually drawn below (at
        # their position in the UI)...
        self.infoBox = InfoBox(widget=self.controlArea)
        self.sendButton = SendButton(
            widget=self.controlArea,
            master=self,
            callback=self.sendData,
            infoBoxAttribute="infoBox",
        )

        # User interface...

        # Browse database box
        browseBox = gui.widgetBox(
            widget=self.controlArea,
            box="Browse database",
            orientation="vertical",
            addSpace=False,
        )

        upwardNavBox = gui.widgetBox(
            widget=browseBox,
            box=False,
            orientation="horizontal",
        )
        self.homeRefreshButton = gui.button(
            widget=upwardNavBox,
            master=self,
            label="Home",
            callback=self.homeRefreshPressed,
        )
        self.backButton = gui.button(
            widget=upwardNavBox,
            master=self,
            label="Back",
            callback=self.backPressed,
            tooltip="View parent folder.",
        )

        gui.separator(widget=browseBox, height=3)

        self.currentFolderLabel = gui.label(
            widget=browseBox,
            master=self,
            label="Current folder: /",
            tooltip="This is the currently displayed folder.",
        )

        gui.separator(widget=browseBox, height=3)

        displayedFolderListbox = gui.listBox(
            widget=browseBox,
            master=self,
            value="selectedInDisplayedFolder", 
            labels="displayedFolderLabels",
            callback=self.corpusSelected,
            tooltip="Select an item to open or import.",
        )
        displayedFolderListbox.setMinimumHeight(120)
        displayedFolderListbox.setSelectionMode(3)
        displayedFolderListbox.doubleClicked.connect(
            self.displayedFoldersDoubleClicked
        )

        gui.separator(widget=browseBox, height=3)

        downwardNavBox = gui.widgetBox(
            widget=browseBox,
            box=False,
            orientation="horizontal",
        )
        self.openButton = gui.button(
            widget=downwardNavBox,
            master=self,
            label="Open",
            callback=self.openPressed,
            tooltip="View selected folder's contents.",
        )
        self.addButton = gui.button(
            widget=downwardNavBox,
            master=self,
            label="Add to selection",
            callback=self.addPressed,
            tooltip="Add selected items contents to selection.",
        )

        gui.separator(widget=browseBox, height=3)

        # Selection box...
        selectionBox = gui.widgetBox(
            widget=self.controlArea,
            box="Selection",
            orientation="vertical",
            addSpace=False,
        )

        selectionListbox = gui.listBox(
            widget=selectionBox,
            master=self,
            value="selectedInSelection",
            labels="selectionLabels",
            callback=lambda: self.removeButton.setDisabled( # TODO move
                self.selectedInSelection == list()),
            tooltip="The list of corpora whose content will be imported",
        )
        selectionListbox.setMinimumHeight(120)
        selectionListbox.setSelectionMode(3)
        selectionListbox.doubleClicked.connect(self.selectionDoubleClicked)

        removalBox = gui.widgetBox(
            widget=selectionBox,
            box=False,
            orientation="horizontal",
        )
        # Remove songs button
        self.removeButton = gui.button(
            widget=removalBox,
            master=self,
            label="Remove from selection",
            callback=self.removePressed,
            tooltip="Remove the selected corpus.",
        )

        # Delete all confirmed songs button
        self.clearButton = gui.button(
            widget=removalBox,
            master=self,
            label="Clear selection",
            callback=self.clearPressed,
            tooltip="Remove all corpora from selection.",
        )

        gui.separator(widget=selectionBox, height=3)

        # Options box...
        optionsBox = gui.widgetBox(
            widget=self.controlArea,
            box="Options",
            orientation="vertical",
            addSpace=False,
        )
        
        gui.checkBox(
            widget=optionsBox,
            master=self,
            value='outputUtterances',
            label=u'Output utterance segmentation',
            callback=self.sendButton.settingsChanged,
            tooltip=u"Toggle emission of utterance segmentation on or off.",
        )

        gui.checkBox(
            widget=optionsBox,
            master=self,
            value='outputWords',
            label=u'Output word segmentation',
            callback=self.toggleWordOptions,
            tooltip=u"Toggle emission of word segmentation on or off.",
        )

        self.wordOptionsBox = gui.indentedBox(
            widget=optionsBox,
            orientation="horizontal",
            addSpace=False,
        )

        gui.label(
            widget=self.wordOptionsBox,
            master=self,
            labelWidth=120,
            label="Word stem includes: ",
            tooltip="TODO.",
        )

        gui.checkBox(
            widget=self.wordOptionsBox,
            master=self,
            labelWidth=70,
            value='includePOSTag',
            label=u'POS-tag',
            callback=self.sendButton.settingsChanged,
            tooltip=u"TODO.",
        )

        gui.checkBox(
            widget=self.wordOptionsBox,
            master=self,
            labelWidth=70,
            value='includePrefixes',
            label=u'prefixes',
            callback=self.sendButton.settingsChanged,
            tooltip=u"TODO.",
        )

        gui.rubber(self.wordOptionsBox)
        
        gui.separator(widget=optionsBox, height=3)

        gui.rubber(self.controlArea)
        
        # Now Info box and Send button must be drawn...
        self.sendButton.draw()
        self.infoBox.draw()

        # This initialization step needs to be done after infoBox has been
        # drawn (because we may need to display an error message).
        self.loadDatabaseCache()

        self.updateSelection()

        # Send data if autoSend.
        self.sendButton.sendIf()

        self.setMinimumWidth(350)
        self.adjustSizeWithTimer()

    def sendData(self):
        """Compute result of widget processing and send to output"""
        if not self.importedCorpora:
            self.infoBox.setText(
                "Please select a corpus to import.",
                "warning"
            )
            self.send("Files", None, self)
            self.send("Utterances", None, self)
            return
       
        # Clear created Inputs and initialize progress bar...
        self.clearCreatedInputs()
        self.infoBox.setText(
            "Retrieving data, please wait...", 
            "warning",
        )     
        self.controlArea.setDisabled(True)
        progressBar = ProgressBar(self, iterations=len(self.importedCorpora))
        
        annotations = list()

        # Iterate over corpora...
        for importedCorpus in self.importedCorpora:
        
            corpus = importedCorpus.split("/")[-1]
            
            # Try to retrieve corpus from cache...
            try:
                basepath = os.path.dirname(
                    os.path.abspath(inspect.getfile(inspect.currentframe()))
                )
                corpusFilepath = os.path.normpath(
                    os.path.join(
                        basepath,
                        self.__class__.cachedFoldername,
                        importedCorpus[len(self.__class__.baseUrl):],
                    )
                )
                inputFile = open(corpusFilepath, "rb")
                myzip = zipfile.ZipFile(inputFile)
                inputFile.close()
            except IOError:                
            
                # Else try to download (and cache) requested zip file...
                try:    
                    response = requests.get(importedCorpus)
                    myZip = zipfile.ZipFile(io.BytesIO(response.content))
                    corpusFolderpath = os.path.dirname(corpusFilepath)
                    try:
                        os.makedirs(corpusFilepath)
                    except OSError:
                        pass
                    try:
                        outputFile = open(corpusFilepath, "wb")
                        outputFile.write(myZip)
                        outputFile.close()
                    except IOError:
                        pass
                    
                # If an error occurs (e.g. connection error)...
                except:

                    # Set Info box and widget to "error" state.
                    self.infoBox.setText(
                        "Couldn't download corpus %s from CHILDES website."
                            % corpus,
                        "error"
                    )

                    # Reset output channel.
                    self.send("Files", None, self)
                    self.send("Utterances", None, self)
                    progressBar.finish()
                    self.controlArea.setDisabled(False)
                    return
            
            # Create Input for each zipped file and store annotations...
            for file in myZip.infolist():
                newInput = Input(
                    myZip.read(file).decode('utf-8'), 
                    self.captionTitle + "_files"
                )
                self.createdInputs.append(newInput)
                chatSeg = Segmenter.import_xml(newInput, "CHAT")
                annotations.append(dict())
                annotations[-1]["file_path"] = file.filename
                for key in ["Corpus", "Lang", "PID"]:
                    try:
                        annotations[-1][key.lower()] =  \
                            chatSeg[0].annotations[key]
                    except KeyError:
                        pass
                participantListSeg = Segmenter.import_xml(
                    newInput, "Participants"
                )
                recodedInput, _ = Segmenter.recode(
                    participantListSeg, [
                        (re.compile("/>"), "> </participant>")
                    ]
                )
                participantSeg = Segmenter.import_xml(
                    recodedInput, "participant"
                )
                targetChildData = list()
                for participant in participantSeg:
                    if participant.annotations["role"] != "Target_Child":
                        continue
                    targetChildData.append(dict())
                    if "age" in participant.annotations:
                        targetChildData[-1]["target_child_age"] =   \
                            participant.annotations["age"]
                    if "id" in participant.annotations:
                        targetChildData[-1]["target_child_id"] =   \
                            participant.annotations["id"]
                    if "sex" in participant.annotations:
                        targetChildData[-1]["target_child_sex"] =   \
                            participant.annotations["sex"]
                if len(targetChildData) == 1:
                    annotations[-1].update(targetChildData[0])
                    
            progressBar.advance()

        # If there's only one file, the widget's output is the created Input...
        if len(self.createdInputs) == 1:
            self.fileSegmentation = self.createdInputs[0]

        # Otherwise the widget's output is a concatenation...
        else:
            self.fileSegmentation = Segmenter.concatenate(
                self.createdInputs,
                self.captionTitle + "_files",
                import_labels_as=None,
            )

        # Annotate segments...
        for idx, segment in enumerate(self.fileSegmentation):
            segment.annotations.update(annotations[idx])
            self.fileSegmentation[idx] = segment

        # Terminate progress bar...
        progressBar.finish()

        message = "%i file@p" % len(self.fileSegmentation)
        message = pluralize(message, len(self.fileSegmentation))
        self.send("Files", self.fileSegmentation, self)
        
        # Build utterance segmentation if needed...
        if self.outputUtterances:
            self.infoBox.setText(
                "Building utterance segmentation, please wait...", 
                "warning",
            )     
            progressBar = ProgressBar(
                self, 
                iterations=len(self.fileSegmentation)
            )
            self.utteranceSegmentation = Segmenter.import_xml(
                self.fileSegmentation,
                "u",
                progress_callback=progressBar.advance,
                label=self.captionTitle + "_utterances",
            )
            progressBar.finish()
            message += " and " if not self.outputWords else ", "
            message += "%i utterance@p" % len(self.utteranceSegmentation)
            message = pluralize(message, len(self.utteranceSegmentation))
            self.send("Utterances", self.utteranceSegmentation, self)
        else:
            self.send("Utterances", None, self)

        # Build word segmentation if needed...
        if self.outputWords:
            self.infoBox.setText(
                "Building word segmentation, please wait...", 
                "warning",
            )     
            progressBar = ProgressBar(
                self, 
                iterations=2*len(self.fileSegmentation)
            )
            wordSegmentation = Segmenter.import_xml(
                self.fileSegmentation,
                "w",
                progress_callback=progressBar.advance,
            )
            replSegmentation = Segmenter.import_xml(
                self.fileSegmentation,
                "replacement",
                progress_callback=progressBar.advance,
            )
            self.infoBox.setText(
                "Handling word replacements, please wait...", 
                "warning",
            )     
            progressBar.finish()
            progressBar = ProgressBar(
                self, 
                iterations=len(self.fileSegmentation) + len(replSegmentation)
            )
            replWordSegmentation = Segmenter.import_xml(
                replSegmentation,
                "w",
                progress_callback=progressBar.advance,
            )
            mwSegmentation = Segmenter.import_xml(
                self.fileSegmentation,
                "mw",
                progress_callback=progressBar.advance,
            )
            
            # Analyze words to extract annotations...
            self.infoBox.setText(
                "Extracting word annotations, please wait...", 
                "warning",
            )     
            progressBar.finish()
            progressBar = ProgressBar(
                self, 
                iterations=len(wordSegmentation)
            )
            wordSegments = list()
            for word in wordSegmentation:
                replacements = word.get_contained_segments(replWordSegmentation)
                wordsOrReplacements = replacements if replacements else [word]
                for wordOrRepl in wordsOrReplacements:
                    mws = wordOrRepl.get_contained_segments(
                        mwSegmentation
                    )
                    if mws:
                        for mw in mws:
                            wordSegment = wordOrRepl.deepcopy()
                            wordSegment.annotations.update(
                                self.extractWordAnnotations(mw)
                            )
                            wordSegments.append(wordSegment) 
                    else:
                        wordSegments.append(wordOrRepl)
                progressBar.advance()
                                                    
            self.wordSegmentation = Segmentation(
                wordSegments,
                label=self.captionTitle + "_words",
            )
            
            message += " and %i word@p" % len(self.wordSegmentation)
            message = pluralize(message, len(self.wordSegmentation))
            self.send("Words", self.wordSegmentation, self)
        else:
            self.send("Words", None, self)

        # Set status to OK and report data size...
        message += " sent to output "
        message = pluralize(message, len(self.fileSegmentation))
        numChars = 0
        for segment in self.fileSegmentation:
            segmentLength = len(Segmentation.get_data(segment.str_index))
            numChars += segmentLength
        message += "(%i character@p)." % numChars
        message = pluralize(message, numChars)
        self.infoBox.setText(message)     
        progressBar.finish()
        
        self.controlArea.setDisabled(False)

        self.sendButton.resetSettingsChangedFlag()

    def extractWordAnnotations(self, mw):
        """Extract annotations from a word's mor tag in CHILDES XML format and 
        return an annotated segment.
        """
        root = ET.fromstring(
            "<mw>" + mw.get_content() + "</mw>"
        )
        annotations = dict()
        pos_items = list()
        prefixes = list()
        suffixes = list()
        for child in root:
            if child.tag == "pos":
                for grandchild in child:
                    if grandchild.tag == "c":
                        pos_items.insert(0, grandchild.text)
                    else:
                        pos_items.append(grandchild.text)
            elif child.tag == "stem":
                stem = child.text
            elif child.tag == "mpfx":
                prefixes.append(child.text)
            elif child.tag == "mk":
                if child.attrib["type"] == "sfxf":
                    suffixes.append("&" + child.text)
                elif child.attrib["type"] == "sfx":
                    suffixes.append("-" + child.text)
                elif child.attrib["type"] == "mc":
                    suffixes.append(":" + child.text)
        annotations["pos"] = ":".join(pos_items)
        if prefixes:
            annotations["prefixes"] = "#".join(prefixes)               
            if self.includePrefixes:
                stem = annotations["prefixes"] + "#" + stem
        if suffixes:
            annotations["suffixes"] = "".join(suffixes)               
        if self.includePOSTag:
            stem = annotations["pos"] + "|" + stem
        annotations["stem"] = stem
        return annotations
        
    def homeRefreshPressed(self):
        """Refresh database file tree"""
        if self.currentFolder != self.__class__.baseUrl:
            self.currentFolder = self.__class__.baseUrl
            self.updateDisplayedFolders()
        else:
            self.refreshDatabaseCache()

    def backPressed(self):
        """Display parent folder's contents"""
        self.currentFolder = "/".join(self.currentFolder[:-1].split("/")[:-1])
        self.currentFolder += "/"
        self.updateDisplayedFolders()        

    def corpusSelected(self):
        """Import selected corpus"""
        self.updateBrowseBoxButtons()

    def openPressed(self):
        """Display selected folder's contents"""
        if len(self.selectedInDisplayedFolder) == 1:
            self.currentFolder += self.displayedFolderLabels[
                self.selectedInDisplayedFolder[0]
            ]
            self.updateDisplayedFolders()

    def addPressed(self):
        """Import selected corpora"""
        corpora = list()
        for item in self.selectedInDisplayedFolder:
            label = self.displayedFolderLabels[item]
            self.getZipsFromItem(label, self.currentFolder, corpora)
        self.importedCorpora += corpora
        self.importedCorpora = sorted(list(set((self.importedCorpora))))
        self.addButton.setDisabled(True)
        self.updateSelection()
        self.sendButton.settingsChanged()

    def getZipsFromItem(self, label, folder, zipList):
        """Get selected zip files or those contained in selected folders"""
        if label.endswith(".zip"):
            zipList.append(folder + label)
            return
        else:
            newFolder = self.getFolderContent(folder + label)
            for label in newFolder.keys():
                if label.endswith(".zip"):
                    label = newFolder[label]
                label = label[len(self.__class__.baseUrl):]
                self.getZipsFromItem(label, folder, zipList)
                
    def displayedFoldersDoubleClicked(self):
        """Reroute to 'openPressed' or 'addPressed' as needed"""
        if self.displayedFolderLabels[
            self.selectedInDisplayedFolder[0]
        ].endswith(".zip"):
            self.addPressed()
        else:
            self.openPressed()

    def removePressed(self):
        """Remove selected items"""
        for idx in sorted(self.selectedInSelection, reverse=True):
            del self.importedCorpora[idx]            
        self.updateSelection()
        self.sendButton.settingsChanged()      

    def clearPressed(self):
        """Empty the selection"""
        self.importedCorpora = list()
        self.updateSelection()
        self.sendButton.settingsChanged()      

    def selectionDoubleClicked(self):
        """Reroute to removePressed"""
        self.removePressed()

    def toggleWordOptions(self):
        """Toggle display of word options on or off"""
        self.wordOptionsBox.setDisabled(not self.outputWords)
        self.sendButton.settingsChanged()

    def refreshDatabaseCache(self):
        """Refresh the database cache"""
        self.infoBox.setText(
            "Scraping CHILDES website, please wait...", 
            "warning",
        )     
        progressBar = ProgressBar(self, iterations=1)
        self.controlArea.setDisabled(True)
        
        # Scrape website...
        self.database = dict()
        self.importedCorpora = list()
        try:
            self.recursivelyScrapeUrl(
                self.__class__.baseUrl, 
                self.database,
            )
            # Dump cache to file...
            path = os.path.dirname(
                os.path.abspath(inspect.getfile(inspect.currentframe()))
            )
            try:
                file = open(
                    os.path.join(path, self.__class__.cacheFilename),
                    "wb",
                )
                pickle.dump(self.database, file)
                file.close()
            except IOError:
                self.infoBox.setText(
                    "Couldn't save database to disk.", 
                    "warning",
                )
            self.sendButton.settingsChanged()
        except requests.exceptions.ConnectionError:
            self.infoBox.setText(
                "Error while attempting to scrape the CHILDES website.", 
                "error",
            )
            self.send("Files", None, self)
            self.send("Utterances", None, self)
              
        progressBar.advance()
        progressBar.finish()
        self.currentFolder = self.__class__.baseUrl
        self.updateDisplayedFolders()
        self.updateSelection()
        self.controlArea.setDisabled(False)

    def recursivelyScrapeUrl(self, url, urls):
        """Scrape the CHILDES website recursively"""
        page = requests.get(url)
        soup = BeautifulSoup(page.text, "html.parser")
        links = soup.find_all('a')
        if links is None or len(links) == 0:
            return
        else:
            urls[url] = dict()
            for link in links:
                new_url = url+link["href"]
                if(
                    link["href"].endswith("/") 
                    and len(link["href"]) > 1
                    and not link["href"].startswith("/data-xml/")
                ):
                    self.recursivelyScrapeUrl(new_url, urls[url])
                elif link["href"].endswith(".zip"):
                    urls[url][link["href"]] = new_url
            if len(urls[url]) == 0:
                del urls[url]
        
    def loadDatabaseCache(self):
        """Load the cached database"""
        # Try to open saved file in this module"s directory...
        path = os.path.dirname(
            os.path.abspath(inspect.getfile(inspect.currentframe()))
        )
        try:
            file = open(os.path.join(path, self.__class__.cacheFilename),"rb")
            self.database = pickle.load(file)
            file.close()
            self.currentFolder = self.__class__.baseUrl
            self.updateDisplayedFolders()
        # Else try to rebuild cache from CHILDES website...
        except IOError:
            self.refreshDatabaseCache()

    def updateDisplayedFolders(self):
        """Refresh state of displayed folder listbox"""
        # If database couldn't be loaded...
        if not self.database:
            self.currentFolderLabel.setText(
                "No database loaded, please click 'Refresh'."
            )
            self.homeRefreshButton.setDisabled(False)
            self.homeRefreshButton.setText("Refresh")
            self.backButton.setDisabled(True)
            self.openButton.setDisabled(True)
            self.addButton.setDisabled(True)
            return
        
        # Current folder label...
        currentFolder = self.currentFolder[len(self.__class__.baseUrl)-1:]
        self.currentFolderLabel.setText("Current folder: " + currentFolder)
        
        # Populate listbox...
        folderContent = self.getFolderContent(self.currentFolder)
        displayedFolderLabels = list()
        for item in folderContent.keys():
            if item.endswith(".zip"):
                displayedFolderLabels.append(item)
            else:
                displayedFolderLabels.append(item.split("/")[-2] + "/")
        self.displayedFolderLabels = displayedFolderLabels
        
        # Buttons.
        self.updateBrowseBoxButtons()
            
    def getFolderContent(self, folder):
        folderContent = self.database[self.__class__.baseUrl]
        folder = folder[len(self.__class__.baseUrl)-1:]
        steps = folder[:-1].split("/")[1:]
        for idx, _ in enumerate(steps):
            path = self.__class__.baseUrl + "/".join(steps[:idx+1]) + "/"
            folderContent = folderContent[path]
        return folderContent

    def updateBrowseBoxButtons(self):
        """Refresh state of Browse box buttons"""
        currentFolder = self.currentFolder[len(self.__class__.baseUrl)-1:]
        if currentFolder == "/": # TODO change tooltip
            self.homeRefreshButton.setText("Refresh")
            self.homeRefreshButton.setToolTip(
                "Connect to CHILDES website and refresh corpus list."
            )
            tooltip="Return to database root.",
        else:
            self.homeRefreshButton.setText("Home")
            self.homeRefreshButton.setToolTip("Return to database root.")
        self.backButton.setDisabled(currentFolder == "/")
        self.openButton.setDisabled(
            len(self.selectedInDisplayedFolder) != 1 or 
            self.displayedFolderLabels[
                self.selectedInDisplayedFolder[0]
            ].endswith(".zip")
        )
        self.addButton.setDisabled(not self.selectedInDisplayedFolder) 

    def updateSelection(self):
        """Refresh state of selection listbox"""
        self.selectionLabels = [
            corpus[len(self.__class__.baseUrl)-1:]
            for corpus in self.importedCorpora
        ]
        self.updateRemovalButtons()

    def updateRemovalButtons(self):
        """Refresh state of Browse box buttons"""
        self.removeButton.setDisabled(not self.selectedInSelection)
        self.clearButton.setDisabled(not self.importedCorpora)

    def clearCreatedInputs(self):
        """Delete all Input objects that have been created."""
        for i in self.createdInputs:
            Segmentation.set_data(i[0].str_index, None)
        del self.createdInputs[:]

    def onDeleteWidget(self):
        """Free memory when widget is deleted (overriden method)"""
        self.clearCreatedInputs()

    # The following method need to be copied (without any change) in
    # every Textable widget...
    def setCaption(self, title):
        if 'captionTitle' in dir(self):
            changed = title != self.captionTitle
            super().setCaption(title)
            if changed:
                self.sendButton.settingsChanged()
        else:
            super().setCaption(title)


if __name__ == "__main__":
    import sys
    from PyQt4.QtGui import QApplication
    myApplication = QApplication(sys.argv)
    myWidget = Childes()
    myWidget.show()
    myApplication.exec_()
    myWidget.saveSettings()