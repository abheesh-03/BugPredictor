import * as vscode from 'vscode';
import * as crypto from 'crypto';

let timeout: NodeJS.Timeout | undefined;
const diagnosticCollection = vscode.languages.createDiagnosticCollection('bugpredictor');
const lastAnalyzedHash = new Map<string, string>();

// ---------------------------------------------------------------------------
// Config helpers
// ---------------------------------------------------------------------------
function getApiUrl(): string {
    return vscode.workspace.getConfiguration('bugpredictor').get<string>('apiUrl')
        || 'https://web-production-cb79b.up.railway.app';
}

function getAuthToken(): string {
    return vscode.workspace.getConfiguration('bugpredictor').get<string>('authToken') || '';
}

function getAuthHeaders(): Record<string, string> {
    const token = getAuthToken();
    const headers: Record<string, string> = { 'Content-Type': 'application/json' };
    if (token) { headers['Authorization'] = `Bearer ${token}`; }
    return headers;
}

// ---------------------------------------------------------------------------
// Bug data store
// ---------------------------------------------------------------------------
interface BugItem {
    filename: string;
    line: number;
    severity: string;
    score: number;
    confidence: number;
    message: string;
    uri: vscode.Uri;
    code: string;
}

const bugStore = new Map<string, BugItem[]>();

// ---------------------------------------------------------------------------
// CodeLens Provider
// ---------------------------------------------------------------------------
class BugPredictorCodeLensProvider implements vscode.CodeLensProvider {
    private _onDidChangeCodeLenses = new vscode.EventEmitter<void>();
    readonly onDidChangeCodeLenses = this._onDidChangeCodeLenses.event;

    refresh(): void {
        this._onDidChangeCodeLenses.fire();
    }

    provideCodeLenses(document: vscode.TextDocument): vscode.CodeLens[] {
        const uri = document.uri.toString();
        const bugs = bugStore.get(uri);
        if (!bugs || bugs.length === 0) { return []; }

        const lenses: vscode.CodeLens[] = [];

        for (const bug of bugs) {
            const targetLine = Math.min(bug.line, document.lineCount - 1);
            const range = new vscode.Range(targetLine, 0, targetLine, 0);

            const severityIcon = bug.severity === 'Critical' ? '🔴' : '🟡';
            const shortMsg = bug.message.substring(0, 60) + (bug.message.length > 60 ? '…' : '');

            // Lens 1 — main bug label
            lenses.push(new vscode.CodeLens(range, {
                title: `${severityIcon} BugPredictor [${bug.score}/10, ${bug.confidence}%]: ${shortMsg}`,
                command: 'bugpredictor.fixFromLens',
                arguments: [document.uri, bug]
            }));

            // Lens 2 — fix button
            lenses.push(new vscode.CodeLens(range, {
                title: '$(wrench) Fix with AI',
                command: 'bugpredictor.fixFromLens',
                arguments: [document.uri, bug]
            }));

            // Lens 3 — ignore button
            lenses.push(new vscode.CodeLens(range, {
                title: '$(eye-closed) Ignore',
                command: 'bugpredictor.ignoreFromLens',
                arguments: [document.uri, bug]
            }));
        }

        return lenses;
    }
}

// ---------------------------------------------------------------------------
// TreeView — Bug Scanner Panel
// ---------------------------------------------------------------------------
class BugTreeItem extends vscode.TreeItem {
    constructor(
        public readonly label: string,
        public readonly collapsibleState: vscode.TreeItemCollapsibleState,
        public readonly bug?: BugItem,
        public readonly fileUri?: vscode.Uri
    ) {
        super(label, collapsibleState);

        if (bug) {
            const emoji = bug.severity === 'Critical' ? '🔴' : '🟡';
            this.label = `${emoji} Line ${bug.line + 1} — ${bug.message.substring(0, 50)}`;
            this.description = `Score: ${bug.score}/10 | Confidence: ${bug.confidence}%`;
            this.tooltip = bug.message;
            this.iconPath = new vscode.ThemeIcon(
                bug.severity === 'Critical' ? 'error' : 'warning',
                new vscode.ThemeColor(
                    bug.severity === 'Critical' ? 'errorForeground' : 'editorWarning.foreground'
                )
            );
            this.command = {
                command: 'vscode.open',
                title: 'Go to bug',
                arguments: [bug.uri, { selection: new vscode.Range(bug.line, 0, bug.line, 0) }]
            };
        } else if (fileUri) {
            const bugs = bugStore.get(fileUri.toString()) || [];
            const critCount = bugs.filter(b => b.severity === 'Critical').length;
            const warnCount = bugs.filter(b => b.severity !== 'Critical').length;
            this.description = `${critCount} critical, ${warnCount} warnings`;
            this.iconPath = new vscode.ThemeIcon('file-code');
            this.tooltip = fileUri.fsPath;
        }
    }
}

class BugScannerProvider implements vscode.TreeDataProvider<BugTreeItem> {
    private _onDidChangeTreeData = new vscode.EventEmitter<BugTreeItem | undefined>();
    readonly onDidChangeTreeData = this._onDidChangeTreeData.event;

    refresh(): void { this._onDidChangeTreeData.fire(undefined); }
    getTreeItem(element: BugTreeItem): vscode.TreeItem { return element; }

    getChildren(element?: BugTreeItem): BugTreeItem[] {
        if (!element) {
            if (bugStore.size === 0) {
                const empty = new BugTreeItem('No bugs detected yet', vscode.TreeItemCollapsibleState.None);
                empty.iconPath = new vscode.ThemeIcon('check');
                return [empty];
            }
            const filesWithBugs = Array.from(bugStore.entries()).filter(([, bugs]) => bugs.length > 0);
            if (filesWithBugs.length === 0) {
                const clean = new BugTreeItem('All files clean ✓', vscode.TreeItemCollapsibleState.None);
                clean.iconPath = new vscode.ThemeIcon('pass');
                return [clean];
            }
            return filesWithBugs.map(([uriStr]) => {
                const uri = vscode.Uri.parse(uriStr);
                const filename = uri.fsPath.split('/').pop() || uri.fsPath;
                return new BugTreeItem(filename, vscode.TreeItemCollapsibleState.Expanded, undefined, uri);
            });
        }
        if (element.fileUri) {
            const bugs = bugStore.get(element.fileUri.toString()) || [];
            return bugs.map(bug => new BugTreeItem(bug.message, vscode.TreeItemCollapsibleState.None, bug));
        }
        return [];
    }
}

// ---------------------------------------------------------------------------
// TreeView — Session Stats Panel
// ---------------------------------------------------------------------------
class StatsProvider implements vscode.TreeDataProvider<vscode.TreeItem> {
    private _onDidChangeTreeData = new vscode.EventEmitter<vscode.TreeItem | undefined>();
    readonly onDidChangeTreeData = this._onDidChangeTreeData.event;

    refresh(): void { this._onDidChangeTreeData.fire(undefined); }
    getTreeItem(element: vscode.TreeItem): vscode.TreeItem { return element; }

    getChildren(): vscode.TreeItem[] {
        let totalBugs = 0, criticalBugs = 0, filesScanned = 0;
        for (const [, bugs] of bugStore) {
            if (bugs.length > 0) {
                filesScanned++;
                totalBugs += bugs.length;
                criticalBugs += bugs.filter(b => b.severity === 'Critical').length;
            }
        }
        const token = getAuthToken();
        const apiUrl = getApiUrl();
        const makeItem = (label: string, description: string, icon: string): vscode.TreeItem => {
            const item = new vscode.TreeItem(label);
            item.description = description;
            item.iconPath = new vscode.ThemeIcon(icon);
            return item;
        };
        return [
            makeItem('Files Scanned', `${filesScanned}`, 'files'),
            makeItem('Total Bugs', `${totalBugs}`, 'bug'),
            makeItem('Critical', `${criticalBugs}`, 'error'),
            makeItem('Warnings', `${totalBugs - criticalBugs}`, 'warning'),
            makeItem('Auth', token ? '✓ Token set' : '✗ Anonymous', token ? 'lock' : 'unlock'),
            makeItem('API', apiUrl.includes('railway') ? 'Railway ☁' : 'Local 🖥', 'plug'),
        ];
    }
}

// ---------------------------------------------------------------------------
// Activate
// ---------------------------------------------------------------------------
export function activate(context: vscode.ExtensionContext) {
    console.log('BugPredictor is now active!');

    const bugScannerProvider = new BugScannerProvider();
    const statsProvider = new StatsProvider();
    const codeLensProvider = new BugPredictorCodeLensProvider();

    // Register tree views
    const bugView = vscode.window.createTreeView('bugpredictorPanel', {
        treeDataProvider: bugScannerProvider,
        showCollapseAll: true
    });
    const statsView = vscode.window.createTreeView('bugpredictorStats', {
        treeDataProvider: statsProvider
    });

    // Register CodeLens for all supported languages
    const codeLensDisposable = vscode.languages.registerCodeLensProvider(
        [
            { language: 'python' },
            { language: 'javascript' },
            { language: 'typescript' },
            { language: 'java' },
            { language: 'cpp' },
            { language: 'c' }
        ],
        codeLensProvider
    );

    context.subscriptions.push(bugView, statsView, codeLensDisposable);

    // Commands
    context.subscriptions.push(

        vscode.commands.registerCommand('bugpredictor.refreshPanel', () => {
            bugScannerProvider.refresh();
            statsProvider.refresh();
            vscode.window.showInformationMessage('BugPredictor: Panel refreshed!');
        }),

        vscode.commands.registerCommand('bugpredictor.analyzeNow', () => {
            const editor = vscode.window.activeTextEditor;
            if (editor) {
                analyzeDocument(editor.document, true, bugScannerProvider, statsProvider, codeLensProvider);
            } else {
                vscode.window.showWarningMessage('BugPredictor: No active file to analyze!');
            }
        }),

        vscode.commands.registerCommand('bugpredictor.clearBugs', () => {
            bugStore.clear();
            diagnosticCollection.clear();
            bugScannerProvider.refresh();
            statsProvider.refresh();
            codeLensProvider.refresh();
            vscode.window.showInformationMessage('BugPredictor: All bugs cleared!');
        }),

        vscode.commands.registerCommand('bugpredictor.setToken', async () => {
            const token = await vscode.window.showInputBox({
                prompt: 'Paste your BugPredictor JWT token (from the web app)',
                password: true,
                placeHolder: 'eyJhbGci...'
            });
            if (token) {
                await vscode.workspace.getConfiguration('bugpredictor').update(
                    'authToken', token, vscode.ConfigurationTarget.Global
                );
                statsProvider.refresh();
                vscode.window.showInformationMessage('BugPredictor: Auth token saved!');
            }
        }),

        // Fix with AI — triggered from CodeLens
        vscode.commands.registerCommand('bugpredictor.fixFromLens',
            async (uri: vscode.Uri, bug: BugItem) => {
                const document = await vscode.workspace.openTextDocument(uri);
                const editor = await vscode.window.showTextDocument(document);
                const API = getApiUrl();

                await vscode.window.withProgress(
                    {
                        location: vscode.ProgressLocation.Notification,
                        title: 'BugPredictor: Generating fix…',
                        cancellable: false
                    },
                    async () => {
                        try {
                            const response = await fetch(`${API}/fix`, {
                                method: 'POST',
                                headers: getAuthHeaders(),
                                body: JSON.stringify({
                                    filename: bug.filename,
                                    code: bug.code
                                })
                            });

                            if (!response.ok) {
                                vscode.window.showErrorMessage('BugPredictor: Fix request failed.');
                                return;
                            }

                            const data = await response.json() as {
                                fixed_code: string;
                                explanation: string;
                            };

                            if (!data.fixed_code) {
                                vscode.window.showErrorMessage('BugPredictor: No fix returned.');
                                return;
                            }

                            const action = await vscode.window.showInformationMessage(
                                `🔧 Fix ready: ${data.explanation}`,
                                'Apply Fix',
                                'Show Diff',
                                'Dismiss'
                            );

                            if (action === 'Apply Fix') {
                                const fullRange = new vscode.Range(
                                    document.positionAt(0),
                                    document.positionAt(document.getText().length)
                                );
                                await editor.edit(editBuilder => {
                                    editBuilder.replace(fullRange, data.fixed_code);
                                });
                                vscode.window.showInformationMessage('BugPredictor: Fix applied! ✅');

                            } else if (action === 'Show Diff') {
                                const tmpUri = uri.with({ path: uri.path + '.bugpredictor-fix' });
                                await vscode.workspace.fs.writeFile(
                                    tmpUri,
                                    Buffer.from(data.fixed_code, 'utf8')
                                );
                                await vscode.commands.executeCommand(
                                    'vscode.diff',
                                    uri,
                                    tmpUri,
                                    `BugPredictor Fix — ${bug.filename}`
                                );
                                const applyAfterDiff = await vscode.window.showInformationMessage(
                                    'Apply this fix?',
                                    'Apply',
                                    'Discard'
                                );
                                if (applyAfterDiff === 'Apply') {
                                    const fullRange = new vscode.Range(
                                        document.positionAt(0),
                                        document.positionAt(document.getText().length)
                                    );
                                    await editor.edit(editBuilder => {
                                        editBuilder.replace(fullRange, data.fixed_code);
                                    });
                                    vscode.window.showInformationMessage('BugPredictor: Fix applied! ✅');
                                }
                                await vscode.workspace.fs.delete(tmpUri).then(() => {}, () => {});
                            }

                        } catch {
                            vscode.window.showErrorMessage('BugPredictor: Could not connect to API.');
                        }
                    }
                );
            }
        ),

        // Ignore from CodeLens
        vscode.commands.registerCommand('bugpredictor.ignoreFromLens',
            async (uri: vscode.Uri, bug: BugItem) => {
                const API = getApiUrl();
                try {
                    await fetch(`${API}/ignore`, {
                        method: 'POST',
                        headers: getAuthHeaders(),
                        body: JSON.stringify({ code: bug.code, filename: bug.filename })
                    });
                    bugStore.set(uri.toString(), []);
                    diagnosticCollection.set(uri, []);
                    bugScannerProvider.refresh();
                    statsProvider.refresh();
                    codeLensProvider.refresh();
                    vscode.window.showInformationMessage(`BugPredictor: Pattern ignored for ${bug.filename}`);
                } catch {
                    vscode.window.showErrorMessage('BugPredictor: Could not ignore pattern.');
                }
            }
        )
    );

    // Auto-analyze on edit (debounced 2s)
    context.subscriptions.push(
        vscode.workspace.onDidChangeTextDocument(event => {
            clearTimeout(timeout);
            timeout = setTimeout(() => {
                analyzeDocument(event.document, false, bugScannerProvider, statsProvider, codeLensProvider);
            }, 2000);
        })
    );

    // Auto-analyze on save
    context.subscriptions.push(
        vscode.workspace.onDidSaveTextDocument(document => {
            analyzeDocument(document, true, bugScannerProvider, statsProvider, codeLensProvider);
        })
    );

    // Analyze active file on startup
    if (vscode.window.activeTextEditor) {
        analyzeDocument(
            vscode.window.activeTextEditor.document,
            false,
            bugScannerProvider,
            statsProvider,
            codeLensProvider
        );
    }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function getCodeHash(code: string): string {
    return crypto.createHash('md5').update(code).digest('hex');
}

async function getRelatedFilesContext(currentDocument: vscode.TextDocument): Promise<string> {
    const relatedFiles: string[] = [];
    const supportedLanguages = ['python', 'javascript', 'typescript', 'java', 'cpp', 'c'];
    for (const doc of vscode.workspace.textDocuments) {
        if (
            doc.uri.toString() === currentDocument.uri.toString() ||
            !supportedLanguages.includes(doc.languageId) ||
            doc.getText().trim().length < 20
        ) { continue; }
        const filename = doc.fileName.split('/').pop() || '';
        const code = doc.getText().substring(0, 500);
        relatedFiles.push(`// ${filename}\n${code}`);
        if (relatedFiles.length >= 3) { break; }
    }
    if (relatedFiles.length === 0) { return ''; }
    return '\n\nOther open files for context:\n' + relatedFiles.join('\n\n');
}

async function analyzeDocument(
    document: vscode.TextDocument,
    force: boolean,
    bugScannerProvider: BugScannerProvider,
    statsProvider: StatsProvider,
    codeLensProvider: BugPredictorCodeLensProvider
) {
    const supportedLanguages = ['python', 'javascript', 'typescript', 'java', 'cpp', 'c'];
    if (!supportedLanguages.includes(document.languageId)) { return; }

    const code = document.getText();
    if (code.trim().length < 20) { return; }

    const hash = getCodeHash(code);
    const uri = document.uri.toString();
    if (!force && lastAnalyzedHash.get(uri) === hash) { return; }
    lastAnalyzedHash.set(uri, hash);

    const relatedContext = await getRelatedFilesContext(document);
    const codeWithContext = code + relatedContext;
    const API = getApiUrl();

    try {
        const response = await fetch(`${API}/analyze`, {
            method: 'POST',
            headers: getAuthHeaders(),
            body: JSON.stringify({
                filename: document.fileName.split('/').pop(),
                code: codeWithContext
            })
        });

        if (!response.ok) { return; }

        const data = await response.json() as {
            prediction: string;
            snapshot_id: string;
            severity: string;
            score: number;
            confidence: number;
            bug_line: number;
            similar_past_bugs: Array<{
                filename: string;
                code: string;
                error_message: string;
                similarity_score: number;
            }>;
            ignored: boolean;
        };

        if (data.ignored || data.severity === 'None') {
            bugStore.set(uri, []);
            diagnosticCollection.set(document.uri, []);
        } else {
            const bug: BugItem = {
                filename: document.fileName.split('/').pop() || '',
                line: data.bug_line,
                severity: data.severity,
                score: data.score,
                confidence: data.confidence,
                message: data.prediction,
                uri: document.uri,
                code: code
            };
            bugStore.set(uri, [bug]);

            showDiagnostics(
                document,
                data.prediction,
                data.severity,
                data.score,
                data.confidence,
                data.bug_line,
                data.similar_past_bugs
            );

            if (data.snapshot_id && data.snapshot_id !== 'ignored') {
                fetch(`${API}/log-bug`, {
                    method: 'POST',
                    headers: getAuthHeaders(),
                    body: JSON.stringify({
                        snapshot_id: data.snapshot_id,
                        error_message: data.prediction.substring(0, 500)
                    })
                }).catch(() => {});
            }
        }

        bugScannerProvider.refresh();
        statsProvider.refresh();
        codeLensProvider.refresh();

    } catch {
        // Server unreachable — fail silently
    }
}

function extractSummary(errorMessage: string): string {
    const lines = errorMessage.split('\n').map((l: string) => l.trim()).filter(Boolean);
    for (const line of lines) {
        const clean = line.replace(/\*\*/g, '').trim();
        if (clean.length > 10 && !clean.startsWith('#')) { return clean.substring(0, 80); }
    }
    return lines[0]?.replace(/\*\*/g, '').substring(0, 80) || 'similar bug pattern';
}

function showDiagnostics(
    document: vscode.TextDocument,
    prediction: string,
    severityStr: string,
    score: number,
    confidence: number,
    bugLine: number,
    similarBugs: Array<{ filename: string; code: string; error_message: string; similarity_score: number }>
) {
    const isCritical = severityStr === 'Critical';
    const severity = isCritical ? vscode.DiagnosticSeverity.Error : vscode.DiagnosticSeverity.Warning;
    const targetLine = Math.min(bugLine, document.lineCount - 1);
    const lineText = document.lineAt(targetLine).text;
    const range = new vscode.Range(targetLine, 0, targetLine, lineText.length);

    const memoryNote = similarBugs.length > 0
        ? `\n\n📚 Seen before (${Math.round(similarBugs[0].similarity_score * 100)}% match): ${extractSummary(similarBugs[0].error_message)}`
        : '';
    const scoreNote = score > 0 ? ` [Score: ${score}/10, Confidence: ${confidence}%]` : '';
    const cleanPrediction = prediction.replace(/\*\*/g, '').replace(/^#+\s*/gm, '').trim().substring(0, 250);

    const diagnostic = new vscode.Diagnostic(
        range,
        `🐛 BugPredictor${scoreNote}: ${cleanPrediction}${memoryNote}`,
        severity
    );
    diagnostic.source = 'BugPredictor';
    diagnosticCollection.set(document.uri, [diagnostic]);
}

export function deactivate() {
    diagnosticCollection.clear();
}