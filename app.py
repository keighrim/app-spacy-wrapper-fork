"""app.py

Wrapping Spacy NLP to extract tokens, tags, lemmas, sentences, chunks and named
entities.

Usage:

$ python app.py -t example-mmif.json out.json
$ python app.py [--develop]
$ python app.py --dbpedia [--develop]

The first invocation is to just test the app without running a server and
without attempting to link entities to DBPedia. The second and third are to
start a server, which you can ping with

$ curl -H "Accept: application/json" -X POST -d@example-mmif.json http://0.0.0.0:5000/

With the --dbpedia option the app will attempt to link entities to DBPedia.

With the --develop option you get a FLask server running in development mode,
without it Gunicorn will be used for a more stable server.

Normally you would run this in a Docker container, see README.md.

"""

import os
import sys
import collections
import json
import urllib
import argparse

import spacy

from clams.app import ClamsApp
from clams.restify import Restifier
from clams.appmetadata import AppMetadata
from mmif.serialize import Mmif
from mmif.vocabulary import AnnotationTypes, DocumentTypes
from lapps.discriminators import Uri

# Load small English core model
nlp = spacy.load("en_core_web_sm")

VERSION = '0.0.7'
MMIF_VERSION = '0.4.0'
MMIF_PYTHON_VERSION = '0.4.5'
CLAMS_PYTHON_VERSION = '0.4.4'
SPACY_VERSION = '3.0.3'


# default is to not attempt linking to DBPedia
use_dbpedia = False

# We need this to find the text documents in the documents list
TEXT_DOCUMENT = os.path.basename(str(DocumentTypes.TextDocument))

DEBUG = False


class SpacyApp(ClamsApp):

    def _appmetadata(self):
        metadata = AppMetadata(
            identifier='https://apps.clams.ai/spacy_nlp',
            name="Spacy NLP",
            description="Apply spaCy NLP to all text documents in a MMIF file.",
            app_version=VERSION,
            wrappee_version=SPACY_VERSION,
            wrappee_license='MIT',
            mmif_version=MMIF_VERSION,
            license='Apache 2.0'
        )
        metadata.add_input(DocumentTypes.TextDocument)
        metadata.add_output(Uri.TOKEN)
        metadata.add_output(Uri.POS)
        metadata.add_output(Uri.LEMMA)
        metadata.add_output(Uri.NCHUNK)
        metadata.add_output(Uri.SENTENCE)
        metadata.add_output(Uri.NE)
        return metadata

    def _annotate(self, mmif, **kwargs):
        Identifiers.reset()
        self.mmif = mmif if type(mmif) is Mmif else Mmif(mmif)
        for doc in text_documents(self.mmif.documents):
            new_view = self._new_view(doc.id)
            self._add_tool_output(doc, new_view)
        for view in list(self.mmif.views):
            docs = self.mmif.get_documents_in_view(view.id)
            if docs:
                new_view = self._new_view()
                for doc in docs:
                    doc_id = view.id + ':' + doc.id
                    self._add_tool_output(doc, new_view, doc_id=doc_id)
        return self.mmif

    def _new_view(self, docid=None):
        view = self.mmif.new_view()
        self.sign_view(view)
        for attype in (Uri.TOKEN, Uri.POS, Uri.LEMMA,
                       Uri.NCHUNK, Uri.SENTENCE, Uri.NE):
            view.new_contain(attype, document=docid)
        return view

    def _read_text(self, textdoc):
        """Read the text content from the document or the text value."""
        if textdoc.location:
            fh = urllib.request.urlopen(textdoc.location)
            text = fh.read().decode('utf8')
        else:
            text = textdoc.properties.text.value
        if DEBUG:
            print('>>> %s%s' % (text.strip()[:100],
                                ('...' if len(text) > 100 else '')))
        return text

    def _add_tool_output(self, doc, view, doc_id=None):
        spacy_doc = nlp(self._read_text(doc))
        dbpedia_ents = {}
        if use_dbpedia:
            dbpedia_ents = {d.text:d.kb_id_ for d in spacy_doc.spans['dbpedia_ents']}
        # keep track of char offsets of all tokens
        tok_idx = {}
        for (n, tok) in enumerate(spacy_doc):
            p1 = tok.idx
            p2 = p1 + len(tok.text)
            tok_idx[n] = (p1, p2)
            add_annotation(
                view, Uri.TOKEN, Identifiers.new("t"),
                doc_id, p1, p2,
                { "pos": tok.tag_, "lemma": tok.lemma_, "text": tok.text })
        for (n, chunk) in enumerate(spacy_doc.noun_chunks):
            add_annotation(
                view, Uri.NCHUNK, Identifiers.new("nc"),
                doc_id, tok_idx[chunk.start][0], tok_idx[chunk.end - 1][1],
                { "text": chunk.text })
        for (n, sent) in enumerate(spacy_doc.sents):
            add_annotation(
                view, Uri.SENTENCE, Identifiers.new("s"),
                doc_id, tok_idx[sent.start][0], tok_idx[sent.end - 1][1],
                { "text": sent.text })
        for (n, ent) in enumerate(spacy_doc.ents):
            try:
                add_annotation(
                    view, Uri.NE, Identifiers.new("ne"),
                    doc_id, tok_idx[ent.start][0], tok_idx[ent.end - 1][1],
                    { "text": ent.text, "category": ent.label_, "kb_id": dbpedia_ents[ent.text]})
            except KeyError:
                add_annotation(
                    view, Uri.NE, Identifiers.new("ne"),
                    doc_id, tok_idx[ent.start][0], tok_idx[ent.end - 1][1],
                    {"text": ent.text, "category": ent.label_})

    def print_documents(self):
        for doc in self.mmif.documents:
            print("%s %s location=%s text=%s" % (
                doc.id, doc.at_type, doc.location, doc.properties.text.value))


def text_documents(documents):
    """Utility method to get all text documents from a list of documents."""
    return [doc for doc in documents if str(doc.at_type).endswith(TEXT_DOCUMENT)]
    # TODO: replace with the following line and remove TEXT_DOCUMENT variable
    # when mmif-python is updated
    # return [doc for doc in documents if doc.is_type(DocumentTypes.TextDocument)]


def add_annotation(view, attype, identifier, doc_id, start, end, properties):
    """Utility method to add an annotation to a view."""
    a = view.new_annotation(attype, identifier)
    if doc_id is not None:
        a.add_property('document', doc_id)
    a.add_property('start', start)
    a.add_property('end', end)
    for prop, val in properties.items():
        a.add_property(prop, val)


class Identifiers(object):

    """Utility class to generate annotation identifiers. You could, but don't have
    to, reset this each time you start a new view. This works only for new views
    since it does not check for identifiers of annotations already in the list
    of annotations."""

    identifiers = collections.defaultdict(int)

    @classmethod
    def new(cls, prefix):
        cls.identifiers[prefix] += 1
        return "%s%d" % (prefix, cls.identifiers[prefix])

    @classmethod
    def reset(cls):
        cls.identifiers = collections.defaultdict(int)



def test(infile, outfile):
    """Run spacy on an input MMIF file. This bypasses the server and just pings
    the annotate() method on the SpacyApp class. Prints a summary of the views
    in the end result."""
    print(SpacyApp().appmetadata(pretty=True))
    with open(infile) as fh_in, open(outfile, 'w') as fh_out:
        mmif_out_as_string = SpacyApp().annotate(fh_in.read(), pretty=True)
        mmif_out = Mmif(mmif_out_as_string)
        fh_out.write(mmif_out_as_string)
        for view in mmif_out.views:
            print("<View id=%s annotations=%s app=%s>"
                  % (view.id, len(view.annotations), view.metadata['app']))


if __name__ == "__main__":

    parser = argparse.ArgumentParser()
    parser.add_argument('-t', '--test',  action='store_true', help="bypass the server")
    parser.add_argument('--develop',  action='store_true', help="start a development server")
    parser.add_argument('--dbpedia',  action='store_true', help="start a production server")
    parser.add_argument('infile', nargs='?', help="input MMIF file")
    parser.add_argument('outfile', nargs='?', help="output file")
    args = parser.parse_args()

    if args.test:
        test(args.infile, args.outfile)
    else:
        if args.dbpedia:
            use_dbpedia = True
            nlp.add_pipe(
                'dbpedia_spotlight',
                config={'overwrite_ents':False,
                        'dbpedia_rest_endpoint': 'http://localhost:2222/rest'})
        app = SpacyApp()
        service = Restifier(app)
        if args.develop:
            service.run()
        else:
            service.serve_production()
