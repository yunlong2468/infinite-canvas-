// 轻量HNSW向量索引——内存构建+SQL.js BLOB持久化
// 适用于千级向量检索场景，零外部依赖

const HNSW = {
  // 超参数
  M: 16,              // 每层每个节点的最大连接数
  efConstruction: 200, // 构建时的搜索宽度
  efSearch: 50,        // 检索时的搜索宽度
  maxLevel: 0,         // 当前最高层级

  // 内部状态
  nodes: [],           // [{id, vector: Float32Array, level, neighbors: [[],[]...]}]
  entryPoint: -1,      // 入口节点索引
  idToIndex: {},       // source_id → nodes数组索引

  // 初始化（从持久化数据加载）
  init(chunks) {
    this.nodes = [];
    this.idToIndex = {};
    this.entryPoint = -1;
    this.maxLevel = 0;
    if (chunks && chunks.length) {
      chunks.forEach(function(c) {
        if (c.embedding_blob && c.embedding_dim) {
          var vec = _blobToVector(c.embedding_blob, c.embedding_dim);
          if (vec) HNSW.insert((c.project_id||0)+':'+c.source_type+':'+c.source_id, vec, c.project_id || 0);
        }
      });
      console.log('[HNSW] 从DB加载 '+chunks.length+' 条记录，索引节点数='+this.nodes.length);
    }
  },

  // 插入向量
  insert(id, vector, projectId) {
    projectId = projectId || 0;
    if (this.idToIndex[id] !== undefined) {
      // 已存在 → 更新向量
      var idx = this.idToIndex[id];
      this.nodes[idx].vector = vector;
      return;
    }
    // 随机层级（指数分布）
    var level = _randomLevel(this.M);
    if (level > this.maxLevel) this.maxLevel = level;

    var nodeIndex = this.nodes.length;
    var node = { id: id, vector: vector, level: level, neighbors: [], projectId: projectId };
    for (var l = 0; l <= level; l++) node.neighbors[l] = [];
    this.nodes.push(node);
    this.idToIndex[id] = nodeIndex;

    if (this.entryPoint === -1) {
      this.entryPoint = nodeIndex;
      return;
    }

    // 从顶层向下逐层搜索并连接
    var ep = this.entryPoint;
    var curDist = _cosineDist(vector, this.nodes[ep].vector);
    for (var lc = this.maxLevel; lc > level; lc--) {
      var changed = true;
      while (changed) {
        changed = false;
        for (var ni = 0; ni < this.nodes[ep].neighbors[lc].length; ni++) {
          var nbr = this.nodes[ep].neighbors[lc][ni];
          var d = _cosineDist(vector, this.nodes[nbr].vector);
          if (d < curDist) { curDist = d; ep = nbr; changed = true; }
        }
      }
    }

    // 在每层连接新节点
    for (var l = Math.min(level, this.maxLevel); l >= 0; l--) {
      var candidates = _searchLayer(vector, ep, this.efConstruction, l);
      var Mmax = l === 0 ? this.M * 2 : this.M;
      _selectNeighbors(candidates, Mmax);
      // 双向连接
      for (var ci = 0; ci < candidates.length; ci++) {
        var c = candidates[ci];
        this.nodes[nodeIndex].neighbors[l].push(c.index);
        this.nodes[c.index].neighbors[l].push(nodeIndex);
        // 修剪邻居超出的连接
        if (this.nodes[c.index].neighbors[l].length > Mmax) {
          _pruneNeighbors(this.nodes[c.index], l, Mmax);
        }
      }
      ep = candidates.length > 0 ? candidates[0].index : ep;
    }
  },

  // K近邻检索
  search(queryVec, k, projectId) {
    projectId = projectId || 0;
    k = k || 10;
    if (this.nodes.length === 0) return [];
    if (this.nodes.length <= k) {
      // 数据量小时直接全量计算
      var all = [];
      for (var i = 0; i < this.nodes.length; i++) {
        all.push({ index: i, id: this.nodes[i].id, dist: _cosineDist(queryVec, this.nodes[i].vector) });
      }
      all.sort(function(a, b) { return a.dist - b.dist; });
      return all.slice(0, k);
    }

    var ep = this.entryPoint;
    var curDist = _cosineDist(queryVec, this.nodes[ep].vector);
    // 从顶层向下搜索
    for (var l = this.maxLevel; l > 0; l--) {
      var changed = true;
      while (changed) {
        changed = false;
        var nbrs = this.nodes[ep].neighbors[l] || [];
        for (var ni = 0; ni < nbrs.length; ni++) {
          var d = _cosineDist(queryVec, this.nodes[nbrs[ni]].vector);
          if (d < curDist) { curDist = d; ep = nbrs[ni]; changed = true; }
        }
      }
    }
    // 在最底层做宽搜索
    var layerResults = _searchLayer(queryVec, ep, this.efSearch, 0);
    if (projectId > 0) layerResults = layerResults.filter(function(r) { return HNSW.nodes[r.index].projectId === projectId || HNSW.nodes[r.index].projectId === 0 || HNSW.nodes[r.index].projectId === undefined; });
    return layerResults.slice(0, k);
  },

  // 获取统计信息
  stats() {
    return { nodeCount: this.nodes.length, maxLevel: this.maxLevel, entryPoint: this.entryPoint };
  }
};

// 随机层级（指数分布概率）
function _randomLevel(M) {
  var r = Math.random();
  return Math.floor(-Math.log(r) * (1 / Math.log(M)));
}

// 余弦距离（1 - 余弦相似度，值越小越相似）
function _cosineDist(a, b) {
  var dot = 0, na = 0, nb = 0;
  for (var i = 0; i < a.length; i++) {
    dot += a[i] * b[i];
    na += a[i] * a[i];
    nb += b[i] * b[i];
  }
  if (na === 0 || nb === 0) return 1;
  return 1 - dot / Math.sqrt(na * nb);
}

// 在某层搜索最近邻
function _searchLayer(query, entryIdx, ef, level) {
  var visited = {};
  var entryNode = HNSW.nodes[entryIdx]; var candidates = [{ index: entryIdx, id: entryNode.id, dist: _cosineDist(query, entryNode.vector) }];
  var results = [candidates[0]];
  visited[entryIdx] = true;

  while (candidates.length > 0) {
    // 取最近的候选
    candidates.sort(function(a, b) { return a.dist - b.dist; });
    var c = candidates.shift();
    // 如果当前候选比最远的结果还远，停止
    if (results.length >= ef && c.dist > results[results.length - 1].dist) break;

    var nbrs = HNSW.nodes[c.index].neighbors[level] || [];
    for (var i = 0; i < nbrs.length; i++) {
      var nIdx = nbrs[i];
      if (visited[nIdx]) continue;
      visited[nIdx] = true;
      var d = _cosineDist(query, HNSW.nodes[nIdx].vector);
      var item = { index: nIdx, id: HNSW.nodes[nIdx].id, dist: d };
      // 插入到结果（保持有序）
      var inserted = false;
      for (var ri = 0; ri < results.length; ri++) {
        if (d < results[ri].dist) { results.splice(ri, 0, item); inserted = true; break; }
      }
      if (!inserted) results.push(item);
      if (results.length > ef) results.pop();
      // 插入到候选
      inserted = false;
      for (var ci2 = 0; ci2 < candidates.length; ci2++) {
        if (d < candidates[ci2].dist) { candidates.splice(ci2, 0, item); inserted = true; break; }
      }
      if (!inserted) candidates.push(item);
    }
  }
  return results;
}

// 选择最近的M个邻居
function _selectNeighbors(candidates, M) {
  candidates.sort(function(a, b) { return a.dist - b.dist; });
  if (candidates.length > M) candidates.length = M;
}

// 修剪节点在某层的连接
function _pruneNeighbors(node, level, Mmax) {
  var nbrs = node.neighbors[level];
  // 计算每个邻居到该节点的距离
  var scored = nbrs.map(function(ni) {
    return { index: ni, dist: _cosineDist(node.vector, HNSW.nodes[ni].vector) };
  });
  scored.sort(function(a, b) { return a.dist - b.dist; });
  node.neighbors[level] = scored.slice(0, Mmax).map(function(s) { return s.index; });
}

// BLOB ↔ Float32Array 转换
function _blobToVector(blob, dim) {
  if (!blob || !dim) return null;
  var buf = blob.buffer || blob;
  if (buf instanceof ArrayBuffer) return new Float32Array(buf);
  return null;
}

function vectorToBlob(vec) {
  return new Uint8Array(vec.buffer);
}

// 导出
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { HNSW: HNSW, vectorToBlob: vectorToBlob };
}
